"""Google OAuth and Gmail operations executed inside AgentCore Runtime.

Provider access tokens never cross this module's public return boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Any, Protocol

from .mail_content import MAX_SOURCE_TEXT_CHARS, extract_mail_source, redact_credentials
from .models import (
    MAX_CASE_SOURCE_TEXT_CHARS,
    EvidenceRecord,
    ResolvedGmailCaseEvidence,
)

GOOGLE_PROVIDER_NAME_ENV = "QUIETPILOT_GOOGLE_PROVIDER_NAME"
GMAIL_TOPIC_NAME_ENV = "QUIETPILOT_GMAIL_TOPIC_NAME"
GOOGLE_OAUTH_RETURN_URL_ENV = "QUIETPILOT_GOOGLE_OAUTH_RETURN_URL"
DEFAULT_GOOGLE_PROVIDER_NAME = "quietpilot-google"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
CALENDAR_API_ROOT = "https://www.googleapis.com/calendar/v3"
GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
MAX_SYNC_MESSAGES = 8
MAX_HISTORY_PAGES = 5
MAX_INTEREST_TITLES = 32
MAX_CASE_EVIDENCE_REFS = 8
CASE_EVIDENCE_PAGE_SIZE = 250
MAX_CASE_EVIDENCE_PAGES_PER_DAY = 2
GMAIL_MESSAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class IdentityDataPlane(Protocol):
    def get_resource_oauth2_token(self, **kwargs: object) -> Mapping[str, Any]: ...


class GoogleHttpClient(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        *,
        access_token: str,
        body: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]: ...

    def revoke(self, access_token: str) -> None: ...


class GoogleAuthorizationRequired(RuntimeError):
    """Raised when a runtime operation requires a fresh user consent flow."""


class GoogleApiError(RuntimeError):
    """Google API failure that exposes only the HTTP status, never response data."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Google API returned HTTP {status_code}")
        self.status_code = status_code


class GoogleCaseEvidenceUnavailable(RuntimeError):
    """A fixed, content-free reason why the exact original source is unavailable."""

    def __init__(self, code: str) -> None:
        super().__init__(f"Gmail Case evidence unavailable: {code}")
        self.code = code


@dataclass(frozen=True, slots=True)
class UrlLibGoogleHttpClient:
    timeout_seconds: float = 10.0

    def request_json(
        self,
        method: str,
        url: str,
        *,
        access_token: str,
        body: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        payload = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise GoogleApiError(error.code) from error
        except urllib.error.URLError as error:
            raise RuntimeError("Google API request failed") from error
        if not raw:
            return {}
        value = json.loads(raw)
        if not isinstance(value, Mapping):
            raise TypeError("Google API returned a non-object response")
        return value

    def revoke(self, access_token: str) -> None:
        body = urllib.parse.urlencode({"token": access_token}).encode("ascii")
        request = urllib.request.Request(
            GOOGLE_REVOKE_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ):
                return
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"Google token revoke returned HTTP {error.code}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError("Google token revoke failed") from error


@dataclass(frozen=True, slots=True)
class GoogleConnector:
    identity: IdentityDataPlane
    google: GoogleHttpClient
    provider_name: str = DEFAULT_GOOGLE_PROVIDER_NAME
    gmail_topic_name: str | None = None
    oauth_return_url: str | None = None

    @classmethod
    def from_environment(cls) -> GoogleConnector:
        import boto3

        return cls(
            identity=boto3.client("bedrock-agentcore"),
            google=UrlLibGoogleHttpClient(),
            provider_name=os.environ.get(
                GOOGLE_PROVIDER_NAME_ENV,
                DEFAULT_GOOGLE_PROVIDER_NAME,
            ),
            gmail_topic_name=os.environ.get(GMAIL_TOPIC_NAME_ENV),
            oauth_return_url=_required_https_url(
                os.environ.get(GOOGLE_OAUTH_RETURN_URL_ENV)
            ),
        )

    def authorize(
        self,
        *,
        workload_access_token: str,
        callback_url: str,
        state: str,
        calendar: bool = False,
    ) -> dict[str, object]:
        scopes = (
            [GMAIL_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE]
            if calendar
            else [GMAIL_READONLY_SCOPE]
        )
        response = self.identity.get_resource_oauth2_token(
            workloadIdentityToken=workload_access_token,
            resourceCredentialProviderName=self.provider_name,
            scopes=scopes,
            oauth2Flow="USER_FEDERATION",
            resourceOauth2ReturnUrl=callback_url,
            customState=state,
            customParameters=_google_oauth_parameters(),
            **({"forceAuthentication": True} if calendar else {}),
        )
        if _non_empty_text(response.get("accessToken")):
            return {"status": "TOKEN_AVAILABLE", "scopes": scopes}

        authorization_url = _non_empty_text(response.get("authorizationUrl"))
        session_uri = _non_empty_text(response.get("sessionUri"))
        if authorization_url and session_uri:
            return {
                "status": "AUTHORIZATION_REQUIRED",
                "authorization_url": authorization_url,
                "session_uri": session_uri,
                "scopes": scopes,
            }
        raise RuntimeError(
            "AgentCore Identity returned no token or authorization session"
        )

    def calendar_status(self, *, workload_access_token: str) -> dict[str, object]:
        access_token = self._access_token(workload_access_token, calendar=True)
        account_hash = self._calendar_account(access_token)
        # Ask for no event content; success proves access to the intended API.
        self.google.request_json(
            "GET",
            f"{CALENDAR_API_ROOT}/calendars/primary/events?maxResults=1&fields=kind",
            access_token=access_token,
        )
        return {
            "status": "CONNECTED",
            "account_hash": account_hash,
            "scopes": [CALENDAR_EVENTS_SCOPE],
        }

    def execute_calendar(
        self,
        *,
        workload_access_token: str,
        operation_id: str,
        account_hash: str,
        parameters: Mapping[str, object],
    ) -> dict[str, object]:
        from .google_calendar import GoogleCalendarExecutor

        access_token = self._access_token(workload_access_token, calendar=True)
        if self._calendar_account(access_token) != account_hash:
            return {
                "status": "FAILED",
                "verified": False,
                "error_code": "GOOGLE_ACCOUNT_CHANGED",
                "result_ref": None,
                "html_url": None,
            }
        return GoogleCalendarExecutor(self.google).execute(
            access_token=access_token,
            operation_id=operation_id,
            parameters=parameters,
        )

    def _calendar_account(self, access_token: str) -> str:
        profile = self.google.request_json(
            "GET", f"{GMAIL_API_ROOT}/profile", access_token=access_token
        )
        email_address = _non_empty_text(profile.get("emailAddress"))
        if email_address is None or len(email_address) > 320:
            raise RuntimeError("Google account verification failed")
        return hashlib.sha256(
            email_address.strip().casefold().encode("utf-8")
        ).hexdigest()

    def scan_and_watch(self, *, workload_access_token: str) -> dict[str, object]:
        access_token = self._access_token(workload_access_token)
        setup = self._setup_mail(access_token=access_token)
        page = self._scan_page(access_token=access_token, page_token=None)
        return {**setup, **page}

    def resolve_case_evidence(
        self,
        *,
        workload_access_token: str,
        account_hash: str,
        evidence: list[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        """Re-read only the owner-validated Case's exact hashed Gmail references.

        Source IDs and redacted MIME text are transient. The caller must validate
        the Case owner/revisions before calling and must not persist these bodies.
        Resolve all IDs before reading any body; never return a partial fallback.
        """
        try:
            if not isinstance(account_hash, str) or not re.fullmatch(
                r"[0-9a-f]{64}", account_hash
            ):
                raise ValueError("Invalid account binding")
            if (
                not isinstance(evidence, list)
                or not 1 <= len(evidence) <= MAX_CASE_EVIDENCE_REFS
            ):
                raise ValueError("Invalid Case evidence bound")
            records = [EvidenceRecord.model_validate(item) for item in evidence]
            if (
                len({record.user_id for record in records}) != 1
                or not records[0].user_id.strip()
                or len({record.ref for record in records}) != len(records)
                or any(
                    record.source != "gmail"
                    or not re.fullmatch(r"gmail:[0-9a-f]{64}", record.ref)
                    for record in records
                )
            ):
                raise ValueError("Invalid Case evidence identity")
            receipts = {
                record.ref: _case_received_millis(record.facts) for record in records
            }
            by_day: dict[int, set[str]] = {}
            for record in records:
                milliseconds = int(receipts[record.ref])
                # Reject unsupported timestamps without using the host's local timezone.
                datetime.fromtimestamp(milliseconds // 1000, UTC)
                by_day.setdefault(milliseconds // 86_400_000, set()).add(record.ref)
        except (TypeError, ValueError, OverflowError, OSError):
            raise GoogleCaseEvidenceUnavailable("INVALID_RECORD") from None

        try:
            access_token = self._access_token(workload_access_token)
            profile = self.google.request_json(
                "GET",
                f"{GMAIL_API_ROOT}/profile?fields=emailAddress",
                access_token=access_token,
            )
            address = _non_empty_text(profile.get("emailAddress"))
            if address is None or len(address) > 320:
                raise GoogleCaseEvidenceUnavailable("READ_FAILED")
            current_account = hashlib.sha256(
                address.strip().casefold().encode("utf-8")
            ).hexdigest()
            if current_account != account_hash:
                raise GoogleCaseEvidenceUnavailable("ACCOUNT_CHANGED")
            resolved: dict[str, str] = {}
            for day, references in by_day.items():
                resolved.update(self._case_message_ids(access_token, day, references))

            result: list[dict[str, object]] = []
            for original in records:
                try:
                    fresh = self._message_evidence(
                        access_token=access_token,
                        message_id=resolved[original.ref],
                        max_source_chars=MAX_CASE_SOURCE_TEXT_CHARS,
                    )
                except GoogleApiError as error:
                    if error.status_code == 404:
                        raise GoogleCaseEvidenceUnavailable("NOT_FOUND") from None
                    raise
                if (
                    fresh.get("ref") != original.ref
                    or fresh.get("source") != "gmail"
                    or fresh.get("title") != original.title
                    or _case_received_millis(fresh.get("facts"))
                    != receipts[original.ref]
                ):
                    raise GoogleCaseEvidenceUnavailable("SOURCE_CHANGED")
                if (
                    "source_content=body" not in fresh.get("facts", [])
                    or not isinstance(fresh.get("untrusted_text"), str)
                    or not fresh["untrusted_text"].strip()
                ):
                    raise GoogleCaseEvidenceUnavailable("SOURCE_UNAVAILABLE")
                # Gmail does not expose the application's evidence revision. Carry
                # the caller-validated revision; never replace it with scanner v1.
                result.append(
                    ResolvedGmailCaseEvidence.model_validate(
                        {
                            **fresh,
                            "user_id": original.user_id,
                            "ref": original.ref,
                            "revision": original.revision,
                        }
                    ).model_dump(mode="json")
                )
            return result
        except (GoogleAuthorizationRequired, GoogleCaseEvidenceUnavailable):
            raise
        except GoogleApiError as error:
            if error.status_code == 401:
                raise GoogleAuthorizationRequired(
                    "Google authorization is required"
                ) from None
            raise GoogleCaseEvidenceUnavailable("READ_FAILED") from None
        except Exception:  # noqa: BLE001 - provider errors may contain private source content
            raise GoogleCaseEvidenceUnavailable("READ_FAILED") from None

    def _case_message_ids(
        self, access_token: str, day: int, references: set[str]
    ) -> dict[str, str]:
        found: dict[str, str] = {}
        seen_ids: set[str] = set()
        seen_tokens: set[str] = set()
        token: str | None = None
        for page in range(MAX_CASE_EVIDENCE_PAGES_PER_DAY):
            parameters: dict[str, object] = {
                "q": f"after:{day * 86400 - 1} before:{(day + 1) * 86400}",
                "maxResults": CASE_EVIDENCE_PAGE_SIZE,
                "fields": "messages/id,nextPageToken",
            }
            if token is not None:
                parameters["pageToken"] = token
            response = self.google.request_json(
                "GET",
                f"{GMAIL_API_ROOT}/messages?{urllib.parse.urlencode(parameters)}",
                access_token=access_token,
            )
            messages = response.get("messages", [])
            if not isinstance(messages, list):
                raise GoogleCaseEvidenceUnavailable("READ_FAILED")
            if len(messages) > CASE_EVIDENCE_PAGE_SIZE:
                raise GoogleCaseEvidenceUnavailable("SEARCH_LIMIT")
            ids: set[str] = set()
            for message in messages:
                message_id = message.get("id") if isinstance(message, Mapping) else None
                if (
                    not isinstance(message_id, str)
                    or GMAIL_MESSAGE_ID_PATTERN.fullmatch(message_id) is None
                ):
                    raise GoogleCaseEvidenceUnavailable("READ_FAILED")
                ids.add(message_id)
                reference = (
                    "gmail:" + hashlib.sha256(message_id.encode("ascii")).hexdigest()
                )
                if reference in references:
                    found[reference] = message_id
            if page and ids and ids <= seen_ids:
                raise GoogleCaseEvidenceUnavailable("REPEATED_PAGE")
            seen_ids.update(ids)
            if references <= found.keys():
                return found
            if "nextPageToken" not in response:
                raise GoogleCaseEvidenceUnavailable("NOT_FOUND")
            token = response["nextPageToken"]
            if (
                not isinstance(token, str)
                or not token
                or len(token) > 2048
                or any(
                    not character.isprintable() or character.isspace()
                    for character in token
                )
            ):
                raise GoogleCaseEvidenceUnavailable("READ_FAILED")
            if token in seen_tokens:
                raise GoogleCaseEvidenceUnavailable("REPEATED_PAGE")
            seen_tokens.add(token)
        raise GoogleCaseEvidenceUnavailable("SEARCH_LIMIT")

    def setup_mail(self, *, workload_access_token: str) -> dict[str, object]:
        """Initialize the account/watch without reading messages or running a model."""

        return self._setup_mail(access_token=self._access_token(workload_access_token))

    def _setup_mail(self, *, access_token: str) -> dict[str, object]:
        profile = self.google.request_json(
            "GET",
            f"{GMAIL_API_ROOT}/profile",
            access_token=access_token,
        )
        history_id = _non_empty_text(profile.get("historyId"))
        if history_id is None:
            raise RuntimeError("Gmail profile response is missing historyId")
        email_address = _non_empty_text(profile.get("emailAddress"))
        if email_address is None or len(email_address) > 320:
            raise RuntimeError("Gmail profile response is missing emailAddress")
        account_hash = hashlib.sha256(
            email_address.strip().casefold().encode("utf-8")
        ).hexdigest()
        # Register the watch before listing mail so messages arriving during the
        # bounded scan are still represented by a later history notification.
        watch = self._start_watch(access_token)
        return {
            "status": "CONNECTED",
            "lookback_days": 7,
            "history_id": watch["history_id"],
            "watch_expiration": watch["expiration"],
            "account_hash": account_hash,
            "scopes": [GMAIL_READONLY_SCOPE],
        }

    def interest_titles(self, *, workload_access_token: str) -> dict[str, object]:
        """Read at most 32 recent INBOX Subjects, with no sender/snippet/body fields."""

        access_token = self._access_token(workload_access_token)
        titles: list[dict[str, str]] = []
        seen: set[str] = set()
        page_token: str | None = None
        sampled = False
        # A fixed page budget also bounds a provider returning sparse/duplicate pages.
        for _ in range(4):
            sampled = False
            parameters: dict[str, object] = {
                "q": "newer_than:7d",
                "labelIds": "INBOX",
                "maxResults": MAX_INTEREST_TITLES - len(seen),
                "fields": "messages/id,nextPageToken",
            }
            if page_token is not None:
                parameters["pageToken"] = page_token
            response = self.google.request_json(
                "GET",
                f"{GMAIL_API_ROOT}/messages?{urllib.parse.urlencode(parameters)}",
                access_token=access_token,
            )
            messages = response.get("messages", [])
            if not isinstance(messages, list) or len(messages) > MAX_INTEREST_TITLES:
                raise TypeError("Gmail title sample records are invalid")
            for message in messages:
                message_id = (
                    _non_empty_text(message.get("id"))
                    if isinstance(message, Mapping)
                    else None
                )
                if (
                    message_id is None
                    or GMAIL_MESSAGE_ID_PATTERN.fullmatch(message_id) is None
                ):
                    raise TypeError("Gmail title sample message ID is invalid")
                if message_id in seen:
                    continue
                if len(seen) >= MAX_INTEREST_TITLES:
                    sampled = True
                    break
                seen.add(message_id)
                title = self._message_title(
                    access_token=access_token, message_id=message_id
                )
                if title is not None:
                    titles.append(title)
            page_token = _non_empty_text(response.get("nextPageToken"))
            if page_token is not None and (
                len(page_token) > 2048
                or any(character.isspace() for character in page_token)
            ):
                raise TypeError("Gmail title sample page token is invalid")
            sampled = sampled or page_token is not None
            if page_token is None or len(seen) >= MAX_INTEREST_TITLES:
                break
        return {"titles": titles, "title_count": len(titles), "sampled": sampled}

    def _message_title(
        self, *, access_token: str, message_id: str
    ) -> dict[str, str] | None:
        query = urllib.parse.urlencode(
            {
                "format": "metadata",
                "metadataHeaders": "Subject",
                "fields": "id,payload/headers(name,value)",
            }
        )
        response = self.google.request_json(
            "GET",
            f"{GMAIL_API_ROOT}/messages/{urllib.parse.quote(message_id, safe='')}?{query}",
            access_token=access_token,
        )
        if _non_empty_text(response.get("id")) != message_id:
            raise RuntimeError("Gmail title response does not match the request")
        payload = response.get("payload")
        headers = payload.get("headers", []) if isinstance(payload, Mapping) else []
        if not isinstance(headers, list):
            raise TypeError("Gmail title headers are invalid")
        subject = None
        for header in headers:
            if not isinstance(header, Mapping):
                continue
            name = _non_empty_text(header.get("name"))
            if name and name.casefold() == "subject":
                subject = _non_empty_text(header.get("value"))
                break
        title = _safe_header(subject, "")
        if not title:
            return None
        return {
            "ref": f"gmail:{hashlib.sha256(message_id.encode('ascii')).hexdigest()}",
            "title": title,
        }

    def scan_page(
        self,
        *,
        workload_access_token: str,
        page_token: str,
    ) -> dict[str, object]:
        access_token = self._access_token(workload_access_token)
        return {
            "status": "SCAN_PAGE",
            **self._scan_page(access_token=access_token, page_token=page_token),
        }

    def disconnect(self, *, workload_access_token: str) -> dict[str, object]:
        try:
            access_token = self._access_token(workload_access_token)
        except GoogleAuthorizationRequired:
            return {"status": "DISCONNECTED"}
        self.google.request_json(
            "POST",
            f"{GMAIL_API_ROOT}/stop",
            access_token=access_token,
        )
        self.google.revoke(access_token)
        return {"status": "DISCONNECTED"}

    def renew_watch(self, *, workload_access_token: str) -> dict[str, object]:
        access_token = self._access_token(workload_access_token)
        watch = self._start_watch(access_token)
        return {
            "status": "WATCH_RENEWED",
            "history_id": watch["history_id"],
            "watch_expiration": watch["expiration"],
        }

    def history_head(self, *, workload_access_token: str) -> dict[str, object]:
        access_token = self._access_token(workload_access_token)
        profile = self.google.request_json(
            "GET",
            f"{GMAIL_API_ROOT}/profile",
            access_token=access_token,
        )
        history_id = _non_empty_text(profile.get("historyId"))
        if history_id is None or not history_id.isdigit():
            raise RuntimeError("Gmail profile response is missing historyId")
        return {"status": "HISTORY_HEAD", "history_id": history_id}

    def sync_history(
        self,
        *,
        workload_access_token: str,
        start_history_id: str,
    ) -> dict[str, object]:
        if not start_history_id.isdigit():
            raise ValueError("Gmail start history ID is invalid")
        access_token = self._access_token(workload_access_token)
        try:
            message_ids, history_id, continuation_required = self._history_ids(
                access_token=access_token,
                start_history_id=start_history_id,
            )
            recovery_mode = "INCREMENTAL"
        except GoogleApiError as error:
            if error.status_code != 404:
                raise
            message_ids, history_id = self._recent_message_ids(
                access_token=access_token
            )
            continuation_required = False
            recovery_mode = "BOUNDED_FULL_SYNC"

        evidence = [
            self._message_evidence(access_token=access_token, message_id=message_id)
            for message_id in message_ids
        ]
        return {
            "status": "SYNCED",
            "history_id": history_id,
            "recovery_mode": recovery_mode,
            "continuation_required": continuation_required,
            "evidence": evidence,
        }

    def _history_ids(
        self,
        *,
        access_token: str,
        start_history_id: str,
    ) -> tuple[list[str], str, bool]:
        message_ids: list[str] = []
        seen: set[str] = set()
        page_token: str | None = None
        latest_history_id = start_history_id
        last_consumed_history_id = start_history_id

        for _ in range(MAX_HISTORY_PAGES):
            parameters = {
                "startHistoryId": start_history_id,
                "historyTypes": "messageAdded",
                "labelId": "INBOX",
                "maxResults": MAX_SYNC_MESSAGES,
            }
            if page_token:
                parameters["pageToken"] = page_token
            response = self.google.request_json(
                "GET",
                f"{GMAIL_API_ROOT}/history?{urllib.parse.urlencode(parameters)}",
                access_token=access_token,
            )
            response_history_id = _non_empty_text(response.get("historyId"))
            if response_history_id is None or not response_history_id.isdigit():
                raise RuntimeError("Gmail history response is incomplete")
            latest_history_id = response_history_id
            history = response.get("history", [])
            if not isinstance(history, list):
                raise TypeError("Gmail history records are invalid")

            for record in history:
                if not isinstance(record, Mapping):
                    raise TypeError("Gmail history record is invalid")
                record_id = _non_empty_text(record.get("id"))
                if record_id is None or not record_id.isdigit():
                    raise TypeError("Gmail history record ID is invalid")
                added = record.get("messagesAdded", [])
                if not isinstance(added, list):
                    raise TypeError("Gmail added-message records are invalid")
                record_consumed = True
                for addition in added:
                    message = (
                        addition.get("message")
                        if isinstance(addition, Mapping)
                        else None
                    )
                    message_id = (
                        _non_empty_text(message.get("id"))
                        if isinstance(message, Mapping)
                        else None
                    )
                    if (
                        message_id is None
                        or GMAIL_MESSAGE_ID_PATTERN.fullmatch(message_id) is None
                    ):
                        raise TypeError("Gmail message ID is invalid")
                    if message_id in seen:
                        continue
                    if len(message_ids) >= MAX_SYNC_MESSAGES:
                        record_consumed = False
                        break
                    seen.add(message_id)
                    message_ids.append(message_id)
                if not record_consumed:
                    return message_ids, last_consumed_history_id, True
                last_consumed_history_id = record_id

            next_page = _non_empty_text(response.get("nextPageToken"))
            if next_page is None:
                return message_ids, latest_history_id, False
            if len(message_ids) >= MAX_SYNC_MESSAGES:
                return message_ids, last_consumed_history_id, True
            page_token = next_page

        return message_ids, last_consumed_history_id, True

    def _recent_message_ids(self, *, access_token: str) -> tuple[list[str], str]:
        profile = self.google.request_json(
            "GET",
            f"{GMAIL_API_ROOT}/profile",
            access_token=access_token,
        )
        history_id = _non_empty_text(profile.get("historyId"))
        if history_id is None or not history_id.isdigit():
            raise RuntimeError("Gmail profile response is missing historyId")
        query = urllib.parse.urlencode(
            {
                "q": "newer_than:7d",
                "labelIds": "INBOX",
                "maxResults": MAX_SYNC_MESSAGES,
            }
        )
        response = self.google.request_json(
            "GET",
            f"{GMAIL_API_ROOT}/messages?{query}",
            access_token=access_token,
        )
        messages = response.get("messages", [])
        if not isinstance(messages, list):
            raise TypeError("Gmail recent-message records are invalid")
        message_ids: list[str] = []
        for message in messages:
            message_id = (
                _non_empty_text(message.get("id"))
                if isinstance(message, Mapping)
                else None
            )
            if (
                message_id is None
                or GMAIL_MESSAGE_ID_PATTERN.fullmatch(message_id) is None
            ):
                raise TypeError("Gmail recent message ID is invalid")
            if message_id not in message_ids:
                message_ids.append(message_id)
        return message_ids, history_id

    def _scan_page(
        self,
        *,
        access_token: str,
        page_token: str | None,
    ) -> dict[str, object]:
        parameters = {
            "q": "newer_than:7d",
            "labelIds": "INBOX",
            "maxResults": MAX_SYNC_MESSAGES,
        }
        if page_token is not None:
            parameters["pageToken"] = page_token
        response = self.google.request_json(
            "GET",
            f"{GMAIL_API_ROOT}/messages?{urllib.parse.urlencode(parameters)}",
            access_token=access_token,
        )
        estimate = response.get("resultSizeEstimate", 0)
        if type(estimate) is not int or estimate < 0:
            raise RuntimeError("Gmail messages response has an invalid result estimate")
        messages = response.get("messages", [])
        if not isinstance(messages, list):
            raise TypeError("Gmail messages response is invalid")
        message_ids: list[str] = []
        for message in messages:
            message_id = (
                _non_empty_text(message.get("id"))
                if isinstance(message, Mapping)
                else None
            )
            if (
                message_id is None
                or GMAIL_MESSAGE_ID_PATTERN.fullmatch(message_id) is None
            ):
                raise TypeError("Gmail recent message ID is invalid")
            if message_id not in message_ids:
                message_ids.append(message_id)
        next_page_token = _non_empty_text(response.get("nextPageToken"))
        if next_page_token is not None and (
            len(next_page_token) > 2048
            or any(character.isspace() for character in next_page_token)
        ):
            raise TypeError("Gmail next-page token is invalid")
        completion_history_id = None
        if next_page_token is None:
            profile = self.google.request_json(
                "GET",
                f"{GMAIL_API_ROOT}/profile",
                access_token=access_token,
            )
            completion_history_id = _non_empty_text(profile.get("historyId"))
            if completion_history_id is None or not completion_history_id.isdigit():
                raise RuntimeError("Gmail scan completion history is unavailable")
        return {
            "recent_message_estimate": estimate,
            "processed_message_count": len(message_ids),
            "next_page_token": next_page_token,
            "completion_history_id": completion_history_id,
            "evidence": [
                self._message_evidence(
                    access_token=access_token,
                    message_id=message_id,
                )
                for message_id in message_ids
            ],
        }

    def _message_evidence(
        self,
        *,
        access_token: str,
        message_id: str,
        max_source_chars: int = MAX_SOURCE_TEXT_CHARS,
    ) -> dict[str, object]:
        query = urllib.parse.urlencode(
            [
                ("format", "full"),
                ("fields", "id,internalDate,snippet,payload"),
            ]
        )
        response = self.google.request_json(
            "GET",
            f"{GMAIL_API_ROOT}/messages/{urllib.parse.quote(message_id, safe='')}?{query}",
            access_token=access_token,
        )
        returned_id = _non_empty_text(response.get("id"))
        if returned_id != message_id:
            raise RuntimeError("Gmail message response does not match the request")
        internal_date = _non_empty_text(response.get("internalDate"))
        if internal_date is None or not internal_date.isdigit():
            raise RuntimeError("Gmail message response is missing internalDate")
        payload = response.get("payload")
        headers = payload.get("headers", []) if isinstance(payload, Mapping) else []
        if not isinstance(headers, list):
            raise TypeError("Gmail message headers are invalid")
        values: dict[str, str] = {}
        for header in headers:
            if not isinstance(header, Mapping):
                continue
            name = _non_empty_text(header.get("name"))
            value = _non_empty_text(header.get("value"))
            if name and value and name.casefold() in {"subject", "from"}:
                values.setdefault(name.casefold(), value)
        title = _safe_header(values.get("subject"), "제목 없는 Gmail 메일")
        sender_domain = _sender_domain(values.get("from"))
        snippet = _non_empty_text(response.get("snippet"))
        content = extract_mail_source(payload, snippet, max_chars=max_source_chars)
        message_hash = hashlib.sha256(message_id.encode("ascii")).hexdigest()
        return {
            "ref": f"gmail:{message_hash}",
            "revision": 1,
            "source": "gmail",
            "title": title,
            "facts": [
                f"received_at_unix_ms={internal_date}",
                f"sender_domain={sender_domain}",
                f"source_content={content.source_content}",
                *(["source_truncated=true"] if content.truncated else []),
            ],
            # Bounded, redacted MIME text is transient model evidence. The Worker
            # persists only normalized title/facts, not this untrusted content.
            "untrusted_text": content.text,
        }

    def _start_watch(self, access_token: str) -> dict[str, str]:
        if not self.gmail_topic_name:
            raise RuntimeError("Gmail Pub/Sub topic is not configured")
        watch = self.google.request_json(
            "POST",
            f"{GMAIL_API_ROOT}/watch",
            access_token=access_token,
            body={
                "labelFilterBehavior": "INCLUDE",
                "labelIds": ["INBOX"],
                "topicName": self.gmail_topic_name,
            },
        )
        history_id = _non_empty_text(watch.get("historyId"))
        expiration = _non_empty_text(watch.get("expiration"))
        if (
            history_id is None
            or not history_id.isdigit()
            or expiration is None
            or not expiration.isdigit()
        ):
            raise RuntimeError("Gmail watch response is incomplete")
        return {"history_id": history_id, "expiration": expiration}

    def _access_token(
        self, workload_access_token: str, *, calendar: bool = False
    ) -> str:
        oauth_return_url = _required_https_url(self.oauth_return_url)
        response = self.identity.get_resource_oauth2_token(
            workloadIdentityToken=workload_access_token,
            resourceCredentialProviderName=self.provider_name,
            scopes=[GMAIL_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE]
            if calendar
            else [GMAIL_READONLY_SCOPE],
            oauth2Flow="USER_FEDERATION",
            resourceOauth2ReturnUrl=oauth_return_url,
            customParameters=_google_oauth_parameters(),
        )
        access_token = _non_empty_text(response.get("accessToken"))
        if access_token is None:
            raise GoogleAuthorizationRequired("Google authorization is required")
        return access_token


def _non_empty_text(value: object) -> str | None:
    return value if isinstance(value, str) and bool(value.strip()) else None


def _case_received_millis(facts: object) -> str:
    if not isinstance(facts, list):
        raise TypeError("Invalid Case receipt timestamp")
    values = [
        fact
        for fact in facts
        if isinstance(fact, str) and fact.startswith("received_at_unix_ms=")
    ]
    if (
        len(values) != 1
        or re.fullmatch(r"received_at_unix_ms=[0-9]{1,15}", values[0]) is None
    ):
        raise ValueError("Invalid Case receipt timestamp")
    return values[0].split("=", 1)[1]


def _google_oauth_parameters() -> dict[str, str]:
    return {"access_type": "offline", "prompt": "consent"}


def _required_https_url(value: object) -> str:
    normalized = _non_empty_text(value)
    if normalized is None:
        raise RuntimeError("Google OAuth return URL is not configured")
    parsed = urllib.parse.urlparse(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise RuntimeError("Google OAuth return URL must be a safe HTTPS URL")
    return normalized


def _safe_header(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    try:
        decoded = str(make_header(decode_header(value)))
    except (HeaderParseError, LookupError, UnicodeError):
        decoded = value
    printable = " ".join(decoded.replace("\r", " ").replace("\n", " ").split())
    printable = "".join(character for character in printable if character.isprintable())
    return redact_credentials(printable)[:200] or fallback


def _sender_domain(value: str | None) -> str:
    if value is None:
        return "unknown"
    address = parseaddr(value)[1].casefold()
    domain = address.rpartition("@")[2]
    if not domain or len(domain) > 253 or re.fullmatch(r"[a-z0-9.-]+", domain) is None:
        return "unknown"
    return domain
