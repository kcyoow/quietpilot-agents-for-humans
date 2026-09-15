"""Owner-bound Google connection orchestration for the control API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

GOOGLE_PROVIDER = "google"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
WATCH_RENEWAL_INTERVAL = timedelta(days=1)
SYNC_MODES = frozenset({"INITIAL_7_DAY", "INCREMENTAL", "BOUNDED_FULL_SYNC"})
STATE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
SESSION_URI_PATTERN = re.compile(
    r"^urn:ietf:params:oauth:request_uri:[A-Za-z0-9._~-]+$"
)


class ConnectionStore(Protocol):
    def get_connection(self, user_id: str) -> dict[str, object] | None: ...

    def put_connection(
        self,
        user_id: str,
        *,
        status: str,
        granted_scopes: list[str],
        scan_progress: int,
        discovery_revision: int,
        lookback_days: int = 7,
        scan_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, object]: ...

    def put_oauth_flow(
        self,
        user_id: str,
        *,
        state: str,
        session_uri: str,
        expires_at: int,
    ) -> None: ...

    def get_oauth_flow(self, state: str) -> dict[str, object] | None: ...

    def complete_oauth_flow(self, state: str) -> None: ...

    def get_oauth_return(self, code: str) -> dict[str, object] | None: ...

    def complete_oauth_return(self, code: str) -> None: ...


class AgentRuntime(Protocol):
    def invoke(
        self, user_id: str, payload: Mapping[str, object]
    ) -> dict[str, object]: ...


class IdentitySessionCompleter(Protocol):
    def complete(self, user_id: str, session_uri: str) -> None: ...


class WorkQueue(Protocol):
    def send(
        self,
        *,
        user_id: str,
        event_type: str,
        payload: Mapping[str, object] | None = None,
    ) -> None: ...


class ConnectionInputError(ValueError):
    pass


class ConnectionFlowNotFound(LookupError):
    pass


class ConnectionFlowConflict(RuntimeError):
    pass


def disconnected_connection() -> dict[str, object]:
    return {
        "provider": GOOGLE_PROVIDER,
        "label": "Google",
        "status": "DISCONNECTED",
        "granted_scopes": [],
        "lookback_days": 7,
        "scan_progress": 0,
        "discovery_revision": 0,
        "error_code": None,
        "last_checked_at": None,
        "last_sync_mode": None,
        "watch_expires_at": None,
        "watch_renewed_at": None,
        "next_renewal_due_at": None,
        "version": 0,
    }


@dataclass(frozen=True, slots=True)
class GoogleConnectionService:
    store: ConnectionStore
    runtime: AgentRuntime
    identity: IdentitySessionCompleter
    queue: WorkQueue
    callback_url: str
    mail: Any = None
    calendar: Any = None

    def list_connections(self, user_id: str) -> list[dict[str, object]]:
        connection = self.store.get_connection(user_id) or disconnected_connection()
        if self.calendar is not None:
            connection = {**connection, "calendar": self.calendar.state(user_id)}
        return [connection]

    def authorize(
        self, user_id: str, *, capability: object = None
    ) -> dict[str, object]:
        if capability == "calendar":
            if self.calendar is None:
                raise ConnectionInputError("Calendar connection is unavailable")
            result = self.calendar.authorize(user_id)
            return {**result, "connection": self.list_connections(user_id)[0]}
        if capability not in (None, "mail"):
            raise ConnectionInputError("Google capability is invalid")
        current = self.store.get_connection(user_id)
        if current is not None and current.get("status") == "REVOKING":
            raise ConnectionFlowConflict("Google disconnection is still in progress")
        state = secrets.token_urlsafe(32)
        result = self.runtime.invoke(
            user_id,
            {
                "operation": "GOOGLE_AUTHORIZE",
                "user_id": user_id,
                "callback_url": self.callback_url,
                "state": state,
            },
        )
        status = result.get("status")
        if status == "TOKEN_AVAILABLE":
            connection = self._start_scan(user_id, lookback_days=7)
            return {"connection": connection}

        authorization_url = _https_url(result.get("authorization_url"))
        session_uri = _session_uri(result.get("session_uri"))
        if (
            status != "AUTHORIZATION_REQUIRED"
            or not authorization_url
            or not session_uri
        ):
            raise RuntimeError(
                "AgentCore returned an invalid Google authorization response"
            )
        self.store.put_oauth_flow(
            user_id,
            state=state,
            session_uri=session_uri,
            expires_at=int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
        )
        connection = self.store.put_connection(
            user_id,
            status="CONNECTING",
            granted_scopes=[],
            scan_progress=0,
            discovery_revision=0,
        )
        return {
            "authorization_url": authorization_url,
            "connection": connection,
        }

    def complete(
        self,
        user_id: str,
        *,
        code: object,
    ) -> dict[str, object]:
        valid_code = _state(code)
        oauth_return = self.store.get_oauth_return(valid_code)
        if oauth_return is None:
            raise ConnectionFlowNotFound("Google authorization session was not found")
        if oauth_return.get("user_id") != user_id:
            raise ConnectionFlowConflict("Google authorization belongs to another user")
        valid_state = _state(oauth_return.get("state"))
        valid_session_uri = _session_uri(oauth_return.get("session_uri"))
        if valid_session_uri is None:
            raise ConnectionFlowConflict("Google authorization session does not match")
        expires_at = oauth_return.get("expires_at")
        if type(expires_at) is not int or expires_at < int(
            datetime.now(UTC).timestamp()
        ):
            raise ConnectionFlowConflict("Google authorization session expired")
        if oauth_return.get("status") == "COMPLETED":
            return self.list_connections(user_id)[0]

        current = self.store.get_connection(user_id)
        if current is not None and current.get("status") == "REVOKING":
            raise ConnectionFlowConflict("Google disconnection is still in progress")
        flow = self.store.get_oauth_flow(valid_state)
        if flow and flow.get("purpose") == "calendar":
            if self.calendar is None:
                raise ConnectionFlowConflict("Calendar connection is unavailable")
            self.calendar.validate_completion(user_id, valid_state)
            if flow.get("status") != "COMPLETED":
                self.identity.complete(user_id, valid_session_uri)
            self.calendar.complete(user_id, valid_state)
            if flow.get("status") != "COMPLETED":
                self.store.complete_oauth_flow(valid_state)
            self.store.complete_oauth_return(valid_code)
            return self.list_connections(user_id)[0]
        self.identity.complete(user_id, valid_session_uri)
        self.store.complete_oauth_flow(valid_state)
        self.store.complete_oauth_return(valid_code)
        return self._start_scan(user_id, lookback_days=7)

    def scan(self, user_id: str, *, lookback_days: object) -> dict[str, object]:
        if _lookback_days(lookback_days) != 7:
            raise ConnectionInputError("mail scan lookback_days must be seven")
        current = self.store.get_connection(user_id)
        if current is None:
            raise ConnectionInputError("Google is not connected")
        status = current.get("status")
        if current.get("error_code") == "GOOGLE_AUTH_REQUIRED":
            return current
        if status not in {"CONNECTED", "ERROR", "SCANNING"}:
            raise ConnectionInputError("Google is not ready to scan")
        scopes = current.get("granted_scopes")
        if not isinstance(scopes, list) or GMAIL_READONLY_SCOPE not in scopes:
            raise ConnectionInputError("Google Gmail scope is unavailable")
        if status == "SCANNING":
            # Old broad-scan events are intentionally ignored by the new Worker.
            # Recover their connection/watch stage; mail reading remains gated
            # by the saved profile in MailJobProcessor.
            return self._start_scan(user_id, lookback_days=7)
        from .mail import default_mail_service

        mail = self.mail or default_mail_service()
        if status == "ERROR":
            from .mail import MailInputError, configured

            if not configured(mail.state(user_id)["profile"]):
                raise MailInputError("Mail interests are not configured")
            return self._start_scan(user_id, lookback_days=7)
        mail.scan(user_id)
        return self.store.get_connection(user_id) or current

    def _start_scan(self, user_id: str, *, lookback_days: int) -> dict[str, object]:
        scan_id = uuid.uuid4().hex
        connection = self.store.put_connection(
            user_id,
            status="CONNECTING",
            granted_scopes=[GMAIL_READONLY_SCOPE],
            scan_progress=0,
            discovery_revision=0,
            lookback_days=lookback_days,
            scan_id=scan_id,
        )
        self.queue.send(
            user_id=user_id,
            event_type="MAIL_SETUP_REQUESTED",
            payload={"scan_id": scan_id},
        )
        return connection

    def disconnect(self, user_id: str) -> dict[str, object]:
        revoke_id = uuid.uuid4().hex
        connection = self.store.put_connection(
            user_id,
            status="REVOKING",
            granted_scopes=[],
            scan_progress=0,
            discovery_revision=0,
            scan_id=revoke_id,
        )
        self.queue.send(
            user_id=user_id,
            event_type="GOOGLE_CONNECTION_REVOKED",
            payload={"request_id": revoke_id},
        )
        return connection


class DynamoConnectionStore:
    def __init__(self, table_name: str, client: Any) -> None:
        self._table_name = table_name
        self._client = client

    @classmethod
    def from_environment(cls) -> DynamoConnectionStore:
        import boto3

        table_name = _required_environment("MAIN_TABLE_NAME")
        return cls(table_name, boto3.client("dynamodb"))

    def get_connection(self, user_id: str) -> dict[str, object] | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=_connection_key(user_id),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not isinstance(item, Mapping):
            return None
        return _connection_from_item(item)

    def put_connection(
        self,
        user_id: str,
        *,
        status: str,
        granted_scopes: list[str],
        scan_progress: int,
        discovery_revision: int,
        lookback_days: int = 7,
        scan_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, object]:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        values: dict[str, object] = {
            ":provider": {"S": GOOGLE_PROVIDER},
            ":label": {"S": "Google"},
            ":status": {"S": status},
            ":scopes": {"L": [{"S": scope} for scope in granted_scopes]},
            ":lookback": {"N": str(lookback_days)},
            ":progress": {"N": str(scan_progress)},
            ":discovery_revision": {"N": str(discovery_revision)},
            ":checked": {"S": now},
            ":zero": {"N": "0"},
            ":one": {"N": "1"},
            ":entity": {"S": "connection"},
        }
        expression = (
            "SET entity_type=:entity, provider=:provider, label=:label, "
            "#status=:status, granted_scopes=:scopes, lookback_days=:lookback, "
            "scan_progress=:progress, discovery_revision=:discovery_revision, "
            "last_checked_at=:checked, "
            "version=if_not_exists(version,:zero)+:one"
        )
        remove_fields: list[str] = []
        if scan_id is None:
            remove_fields.append("gmail_scan_id")
        else:
            expression += ", gmail_scan_id=:scan_id"
            values[":scan_id"] = {"S": scan_id}
        if status == "CONNECTING":
            expression += ", mail_connection_id=:mail_epoch"
            values[":mail_epoch"] = {"S": scan_id or uuid.uuid4().hex}
        elif status in {"REVOKING", "DISCONNECTED"}:
            remove_fields.append("mail_connection_id")
        if error_code is None:
            remove_fields.append("error_code")
        else:
            expression += ", error_code=:error"
            values[":error"] = {"S": error_code}
        if remove_fields:
            expression += f" REMOVE {', '.join(remove_fields)}"
        request: dict[str, Any] = {
            "TableName": self._table_name,
            "Key": _connection_key(user_id),
            "UpdateExpression": expression,
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": values,
            "ReturnValues": "ALL_NEW",
        }
        if status == "CONNECTING":
            # The initial read is not a lock. A disconnect may begin while the
            # OAuth runtime/Identity call is in flight; never overwrite it.
            request["ConditionExpression"] = (
                "attribute_not_exists(#status) OR #status<>:revoking"
            )
            values[":revoking"] = {"S": "REVOKING"}
        try:
            response = self._client.update_item(**request)
        except self._client.exceptions.ConditionalCheckFailedException:
            raise ConnectionFlowConflict("Google connection state changed") from None
        return _connection_from_item(response["Attributes"])

    def put_oauth_flow(
        self,
        user_id: str,
        *,
        state: str,
        session_uri: str,
        expires_at: int,
    ) -> None:
        self._client.put_item(
            TableName=self._table_name,
            Item={
                **_oauth_key(state),
                "entity_type": {"S": "oauth_flow"},
                "provider": {"S": GOOGLE_PROVIDER},
                "user_id": {"S": user_id},
                "session_uri": {"S": session_uri},
                "status": {"S": "PENDING"},
                "expiresAt": {"N": str(expires_at)},
            },
            ConditionExpression="attribute_not_exists(PK)",
        )

    def get_oauth_flow(self, state: str) -> dict[str, object] | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=_oauth_key(state),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not isinstance(item, Mapping):
            return None
        return {
            "user_id": _string_attribute(item, "user_id"),
            "session_uri": _string_attribute(item, "session_uri"),
            "status": _string_attribute(item, "status"),
            "expires_at": _integer_attribute(item, "expiresAt"),
            "purpose": _string_attribute(item, "purpose"),
        }

    def complete_oauth_flow(self, state: str) -> None:
        self._client.update_item(
            TableName=self._table_name,
            Key=_oauth_key(state),
            UpdateExpression="SET #status=:completed",
            ConditionExpression="#status=:pending",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":pending": {"S": "PENDING"},
                ":completed": {"S": "COMPLETED"},
            },
        )

    def get_oauth_return(self, code: str) -> dict[str, object] | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=_oauth_return_key(code),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not isinstance(item, Mapping):
            return None
        return {
            "user_id": _string_attribute(item, "user_id"),
            "session_uri": _string_attribute(item, "session_uri"),
            "state": _string_attribute(item, "state"),
            "status": _string_attribute(item, "status"),
            "expires_at": _integer_attribute(item, "expiresAt"),
        }

    def complete_oauth_return(self, code: str) -> None:
        self._client.update_item(
            TableName=self._table_name,
            Key=_oauth_return_key(code),
            UpdateExpression="SET #status=:completed",
            ConditionExpression="#status=:pending",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":pending": {"S": "PENDING"},
                ":completed": {"S": "COMPLETED"},
            },
        )


class BotoAgentRuntime:
    def __init__(self, runtime_arn: str, client: Any) -> None:
        self._runtime_arn = runtime_arn
        self._client = client

    @classmethod
    def from_environment(cls) -> BotoAgentRuntime:
        import boto3

        return cls(
            _required_environment("AGENTCORE_RUNTIME_ARN"),
            boto3.client("bedrock-agentcore"),
        )

    def invoke(self, user_id: str, payload: Mapping[str, object]) -> dict[str, object]:
        response = self._client.invoke_agent_runtime(
            agentRuntimeArn=self._runtime_arn,
            qualifier="DEFAULT",
            runtimeSessionId=f"quietpilot-{uuid.uuid4()}",
            runtimeUserId=user_id,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )
        if response.get("statusCode") != 200:
            raise RuntimeError("AgentCore Runtime invocation failed")
        stream = response.get("response")
        raw = stream.read() if hasattr(stream, "read") else stream
        if not isinstance(raw, bytes):
            raise TypeError("AgentCore Runtime returned no response body")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError("AgentCore Runtime returned a non-object response")
        return value


class BotoIdentitySessionCompleter:
    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_environment(cls) -> BotoIdentitySessionCompleter:
        import boto3

        return cls(boto3.client("bedrock-agentcore"))

    def complete(self, user_id: str, session_uri: str) -> None:
        self._client.complete_resource_token_auth(
            userIdentifier={"userId": user_id},
            sessionUri=session_uri,
        )


class SqsWorkQueue:
    def __init__(self, queue_url: str, client: Any) -> None:
        self._queue_url = queue_url
        self._client = client

    @classmethod
    def from_environment(cls) -> SqsWorkQueue:
        import boto3

        return cls(_required_environment("WORK_QUEUE_URL"), boto3.client("sqs"))

    def send(
        self,
        *,
        user_id: str,
        event_type: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        event_id = str(uuid.uuid4())
        envelope = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "user_id": user_id,
            "connector": GOOGLE_PROVIDER,
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "dedupe_key": f"{GOOGLE_PROVIDER}:{event_type}:{event_id}",
            "trace_id": str(uuid.uuid4()),
            "payload": dict(payload or {}),
        }
        self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(envelope, separators=(",", ":")),
        )


def default_google_connection_service() -> GoogleConnectionService:
    from .calendar_connection import CalendarConnection

    callback_url = _required_environment("GOOGLE_OAUTH_RETURN_URL")
    if _https_url(callback_url) is None:
        raise RuntimeError("GOOGLE_OAUTH_RETURN_URL must be HTTPS")
    store = DynamoConnectionStore.from_environment()
    runtime = BotoAgentRuntime.from_environment()
    return GoogleConnectionService(
        store=store,
        runtime=runtime,
        identity=BotoIdentitySessionCompleter.from_environment(),
        queue=SqsWorkQueue.from_environment(),
        callback_url=callback_url,
        calendar=CalendarConnection(
            store._table_name, store._client, runtime, callback_url
        ),
    )


def _connection_key(user_id: str) -> dict[str, dict[str, str]]:
    return {"PK": {"S": f"USER#{user_id}"}, "SK": {"S": "CONNECTION#google"}}


def _oauth_key(state: str) -> dict[str, dict[str, str]]:
    digest = hashlib.sha256(state.encode("ascii")).hexdigest()
    return {"PK": {"S": f"OAUTH#{digest}"}, "SK": {"S": "FLOW#google"}}


def _oauth_return_key(code: str) -> dict[str, dict[str, str]]:
    digest = hashlib.sha256(code.encode("ascii")).hexdigest()
    return {"PK": {"S": f"RETURN#{digest}"}, "SK": {"S": "OAUTH#google"}}


def _state(value: object) -> str:
    if not isinstance(value, str) or STATE_PATTERN.fullmatch(value) is None:
        raise ConnectionInputError("state is invalid")
    return value


def _session_uri(value: object) -> str | None:
    if not isinstance(value, str) or SESSION_URI_PATTERN.fullmatch(value) is None:
        return None
    return value


def _https_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 4096:
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return value


def _connection_from_item(item: Mapping[str, Any]) -> dict[str, object]:
    scopes = item.get("granted_scopes", {}).get("L", [])
    status = _string_attribute(item, "status") or "ERROR"
    watch_expires_at = None
    watch_renewed_at = None
    next_renewal_due_at = None
    if status == "CONNECTED":
        watch_expiration = _integer_attribute(item, "watch_expiration")
        expires = _from_epoch_millis(watch_expiration)
        if expires is not None:
            watch_expires_at = _format_utc(expires)
            renewed = _parse_utc(
                _string_attribute(item, "watch_renewed_at")
                or _string_attribute(item, "last_checked_at")
            )
            if renewed is not None:
                watch_renewed_at = _format_utc(renewed)
                renewal_due = min(
                    renewed + WATCH_RENEWAL_INTERVAL,
                    expires - WATCH_RENEWAL_INTERVAL,
                )
                next_renewal_due_at = _format_utc(renewal_due)
    return {
        "provider": _string_attribute(item, "provider") or GOOGLE_PROVIDER,
        "label": _string_attribute(item, "label") or "Google",
        "status": status,
        "granted_scopes": [
            value["S"]
            for value in scopes
            if isinstance(value, Mapping) and "S" in value
        ],
        "lookback_days": _integer_attribute(item, "lookback_days") or 7,
        "scan_progress": _integer_attribute(item, "scan_progress") or 0,
        "discovery_revision": _integer_attribute(item, "discovery_revision") or 0,
        "error_code": _string_attribute(item, "error_code"),
        "last_checked_at": _string_attribute(item, "last_checked_at"),
        "last_sync_mode": _sync_mode(item),
        "watch_expires_at": watch_expires_at,
        "watch_renewed_at": watch_renewed_at,
        "next_renewal_due_at": next_renewal_due_at,
        "version": _integer_attribute(item, "version") or 0,
    }


def _lookback_days(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 30:
        raise ConnectionInputError("lookback_days must be between 1 and 30")
    return value


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _from_epoch_millis(value: int | None) -> datetime | None:
    if value is None or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _sync_mode(item: Mapping[str, Any]) -> str | None:
    value = _string_attribute(item, "last_sync_mode")
    return value if value in SYNC_MODES else None


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _string_attribute(item: Mapping[str, Any], name: str) -> str | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("S"), str):
        return value["S"]
    return None


def _integer_attribute(item: Mapping[str, Any], name: str) -> int | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("N"), str):
        return int(value["N"])
    return None


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value
