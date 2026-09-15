"""Idempotent-enough Google connection jobs behind the SQS boundary."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from .google_connection_store import (
    ACTION_READY_DISCOVERY_REVISION,
    HISTORY_SYNC_LEASE_SECONDS,
    DynamoConnectionWriter,
    HistorySyncClaim,
    HistorySyncInProgress,
    _required_environment,
    _scan_progress,
)

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MAX_INITIAL_SCAN_PAGES = 64


class AgentRuntime(Protocol):
    def invoke(
        self,
        user_id: str,
        operation: str,
        parameters: Mapping[str, object] | None = None,
    ) -> dict[str, object]: ...


class ConnectionWriter(Protocol):
    def write(
        self,
        user_id: str,
        *,
        status: str,
        granted_scopes: list[str],
        scan_progress: int,
        discovery_revision: int,
        details: Mapping[str, object] | None = None,
        scan_id: str | None = None,
    ) -> None: ...

    def get_history_cursor(self, user_id: str) -> str | None: ...

    def is_active_scan(self, user_id: str, *, scan_id: str) -> bool: ...

    def claim_history_sync(
        self,
        user_id: str,
        *,
        notified_history_id: str,
    ) -> HistorySyncClaim | None: ...

    def release_history_sync(self, user_id: str, *, token: str) -> None: ...

    def persist_sync(
        self,
        user_id: str,
        *,
        start_history_id: str,
        history_id: str,
        recovery_mode: str,
        evidence: list[dict[str, object]],
        candidates: list[dict[str, object]],
        sync_token: str | None = None,
    ) -> list[str]: ...

    def persist_scan_page(
        self,
        user_id: str,
        *,
        scan_id: str,
        processed_message_count: int,
        recent_message_estimate: int,
        unresolved_evidence_count: int,
        evidence: list[dict[str, object]],
        candidates: list[dict[str, object]],
        complete: bool,
    ) -> list[str]: ...


class ContinuationQueue(Protocol):
    def send_history(self, *, user_id: str, history_id: str) -> None: ...

    def send_scan_page(
        self,
        *,
        user_id: str,
        scan_id: str,
        page_token: str,
        page_number: int,
        page_token_hashes: list[str],
        processed_message_count: int,
        unresolved_evidence_count: int,
    ) -> None: ...


class GoogleJobProcessor:
    def __init__(
        self,
        runtime: AgentRuntime,
        writer: ConnectionWriter,
        continuation_queue: ContinuationQueue | None = None,
    ) -> None:
        self._runtime = runtime
        self._writer = writer
        self._continuation_queue = continuation_queue

    def process(self, envelope: Mapping[str, object]) -> None:
        if envelope.get("connector") != "google":
            raise ValueError("Google job processor received another connector")
        user_id = envelope.get("user_id")
        if not isinstance(user_id, str) or not user_id or len(user_id) > 128:
            raise ValueError("Google job user_id is invalid")
        event_type = envelope.get("event_type")
        if event_type == "INITIAL_SCAN_REQUESTED":
            payload = envelope.get("payload")
            if not isinstance(payload, Mapping):
                raise TypeError("initial Gmail scan payload is invalid")
            scan_id = _scan_id(payload.get("scan_id"))
            result = self._runtime.invoke(user_id, "GOOGLE_SCAN")
            if result.get("status") != "CONNECTED":
                raise RuntimeError("AgentCore did not complete the Gmail scan")
            details = _scan_details(result)
            page = _scan_page_details(result)
            complete = page["next_page_token"] is None
            unresolved = page["unresolved_evidence_count"]
            progress = _scan_progress(
                page["processed_message_count"],
                page["recent_message_estimate"],
                complete=complete and unresolved == 0,
            )
            self._writer.write(
                user_id,
                status="CONNECTED",
                granted_scopes=[GMAIL_READONLY_SCOPE],
                scan_progress=progress,
                discovery_revision=0,
                details=details,
                scan_id=scan_id,
            )
            if complete:
                if self._continuation_queue is None:
                    raise RuntimeError("Gmail scan catch-up queue is unavailable")
                self._continuation_queue.send_history(
                    user_id=user_id,
                    history_id=page["completion_history_id"],
                )
            self._writer.persist_scan_page(
                user_id,
                scan_id=scan_id,
                processed_message_count=page["processed_message_count"],
                recent_message_estimate=page["recent_message_estimate"],
                unresolved_evidence_count=unresolved,
                evidence=page["evidence"],
                candidates=page["candidates"],
                complete=complete,
            )
            next_page_token = page["next_page_token"]
            if next_page_token is not None:
                if self._continuation_queue is None:
                    raise RuntimeError("Gmail scan continuation queue is unavailable")
                self._continuation_queue.send_scan_page(
                    user_id=user_id,
                    scan_id=scan_id,
                    page_token=next_page_token,
                    page_number=2,
                    page_token_hashes=[_page_token_hash(next_page_token)],
                    processed_message_count=page["processed_message_count"],
                    unresolved_evidence_count=unresolved,
                )
            return
        if event_type == "INITIAL_SCAN_CONTINUATION":
            payload = envelope.get("payload")
            if not isinstance(payload, Mapping):
                raise TypeError("Gmail scan continuation payload is invalid")
            scan_id = _scan_id(payload.get("scan_id"))
            if not self._writer.is_active_scan(user_id, scan_id=scan_id):
                return
            page_token = _page_token(payload.get("page_token"))
            already_processed = _non_negative_integer(
                payload.get("processed_message_count"),
                field_name="processed_message_count",
            )
            already_unresolved = _non_negative_integer(
                payload.get("unresolved_evidence_count"),
                field_name="unresolved_evidence_count",
            )
            page_number = _scan_page_number(
                payload.get("page_number"),
                already_processed=already_processed,
            )
            page_token_hashes = _page_token_hashes(
                payload.get("page_token_hashes"),
                current_page_token=page_token,
            )
            result = self._runtime.invoke(
                user_id,
                "GOOGLE_SCAN_PAGE",
                {"page_token": page_token},
            )
            page = _scan_page_details(result)
            processed = already_processed + page["processed_message_count"]
            unresolved = already_unresolved + page["unresolved_evidence_count"]
            complete = page["next_page_token"] is None
            next_page_token = page["next_page_token"]
            if next_page_token is not None:
                if page_number >= MAX_INITIAL_SCAN_PAGES:
                    raise RuntimeError("Gmail scan page limit exceeded")
                next_page_token_hash = _page_token_hash(next_page_token)
                if next_page_token_hash in page_token_hashes:
                    raise RuntimeError("Gmail scan page token cycle detected")
            if complete:
                if self._continuation_queue is None:
                    raise RuntimeError("Gmail scan catch-up queue is unavailable")
                self._continuation_queue.send_history(
                    user_id=user_id,
                    history_id=page["completion_history_id"],
                )
            self._writer.persist_scan_page(
                user_id,
                scan_id=scan_id,
                processed_message_count=processed,
                recent_message_estimate=page["recent_message_estimate"],
                unresolved_evidence_count=unresolved,
                evidence=page["evidence"],
                candidates=page["candidates"],
                complete=complete,
            )
            if next_page_token is not None:
                if self._continuation_queue is None:
                    raise RuntimeError("Gmail scan continuation queue is unavailable")
                self._continuation_queue.send_scan_page(
                    user_id=user_id,
                    scan_id=scan_id,
                    page_token=next_page_token,
                    page_number=page_number + 1,
                    page_token_hashes=[
                        *page_token_hashes,
                        next_page_token_hash,
                    ],
                    processed_message_count=processed,
                    unresolved_evidence_count=unresolved,
                )
            return
        if event_type == "GOOGLE_CONNECTION_REVOKED":
            result = self._runtime.invoke(user_id, "GOOGLE_DISCONNECT")
            if result.get("status") != "DISCONNECTED":
                raise RuntimeError("AgentCore did not revoke the Google connection")
            self._writer.write(
                user_id,
                status="DISCONNECTED",
                granted_scopes=[],
                scan_progress=0,
                discovery_revision=0,
            )
            return
        if event_type == "GMAIL_HISTORY_AVAILABLE":
            payload = envelope.get("payload")
            if not isinstance(payload, Mapping):
                raise TypeError("Gmail history payload is invalid")
            notified_history_id = _history_id(payload.get("history_id"))
            claim = self._writer.claim_history_sync(
                user_id,
                notified_history_id=notified_history_id,
            )
            if claim is None:
                return
            try:
                result = self._runtime.invoke(
                    user_id,
                    "GOOGLE_HISTORY_SYNC",
                    {"start_history_id": claim.start_history_id},
                )
                sync = _sync_details(result)
                self._writer.persist_sync(
                    user_id,
                    start_history_id=claim.start_history_id,
                    history_id=sync["history_id"],
                    recovery_mode=sync["recovery_mode"],
                    evidence=sync["evidence"],
                    candidates=sync["candidates"],
                    sync_token=claim.token,
                )
            except Exception:
                self._writer.release_history_sync(user_id, token=claim.token)
                raise
            if sync["continuation_required"]:
                if self._continuation_queue is None:
                    raise RuntimeError(
                        "Gmail history continuation queue is unavailable"
                    )
                self._continuation_queue.send_history(
                    user_id=user_id,
                    history_id=notified_history_id,
                )
            return
        raise ValueError("Google job event type is not implemented")


class BotoAgentRuntime:
    def __init__(self, runtime_arn: str, client: Any) -> None:
        self._runtime_arn = runtime_arn
        self._client = client

    @classmethod
    def from_environment(cls) -> BotoAgentRuntime:
        import boto3
        from botocore.config import Config

        return cls(
            _required_environment("AGENTCORE_RUNTIME_ARN"),
            boto3.client(
                "bedrock-agentcore",
                config=Config(
                    connect_timeout=5,
                    read_timeout=90,
                    # A timed-out request can still run remotely. Leave retries
                    # to the worker/SQS boundary instead of invoking it twice.
                    retries={"mode": "standard", "total_max_attempts": 1},
                ),
            ),
        )

    def invoke(
        self,
        user_id: str,
        operation: str,
        parameters: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"operation": operation, "user_id": user_id}
        if parameters:
            payload.update(parameters)
        return self.invoke_payload(user_id, payload)

    def invoke_payload(
        self, user_id: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
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


class SqsContinuationQueue:
    def __init__(self, queue_url: str, client: Any) -> None:
        self._queue_url = queue_url
        self._client = client

    @classmethod
    def from_environment(cls) -> SqsContinuationQueue:
        import boto3

        return cls(_required_environment("WORK_QUEUE_URL"), boto3.client("sqs"))

    def send_history(self, *, user_id: str, history_id: str) -> None:
        event_id = str(uuid.uuid4())
        user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        envelope = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": "GMAIL_HISTORY_AVAILABLE",
            "user_id": user_id,
            "connector": "google",
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "dedupe_key": f"google:gmail-history-continuation:{user_hash}:{history_id}",
            "trace_id": str(uuid.uuid4()),
            "payload": {"history_id": history_id},
        }
        self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(envelope, separators=(",", ":")),
        )

    def send_scan_page(
        self,
        *,
        user_id: str,
        scan_id: str,
        page_token: str,
        page_number: int,
        page_token_hashes: list[str],
        processed_message_count: int,
        unresolved_evidence_count: int,
    ) -> None:
        event_id = str(uuid.uuid4())
        user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        token_hash = hashlib.sha256(page_token.encode("utf-8")).hexdigest()
        envelope = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": "INITIAL_SCAN_CONTINUATION",
            "user_id": user_id,
            "connector": "google",
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "dedupe_key": (
                f"google:gmail-scan-page:{user_hash}:{scan_id}:{token_hash}"
            ),
            "trace_id": str(uuid.uuid4()),
            "payload": {
                "scan_id": scan_id,
                "page_token": page_token,
                "page_number": page_number,
                "page_token_hashes": page_token_hashes,
                "processed_message_count": processed_message_count,
                "unresolved_evidence_count": unresolved_evidence_count,
            },
        }
        self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(envelope, separators=(",", ":")),
        )


def default_google_job_processor() -> Any:
    import boto3

    from .mail_jobs import DynamoMailJobStore, MailJobProcessor, SqsMailQueue

    runtime = BotoAgentRuntime.from_environment()
    writer = DynamoConnectionWriter.from_environment()
    legacy = GoogleJobProcessor(
        runtime, writer, SqsContinuationQueue.from_environment()
    )
    return MailJobProcessor(
        runtime,
        writer,
        DynamoMailJobStore(
            _required_environment("MAIN_TABLE_NAME"), boto3.client("dynamodb")
        ),
        SqsMailQueue(_required_environment("WORK_QUEUE_URL"), boto3.client("sqs")),
        legacy,
    )


def _scan_details(result: Mapping[str, object]) -> dict[str, object]:
    estimate = result.get("recent_message_estimate")
    history_id = result.get("history_id")
    watch_expiration = result.get("watch_expiration")
    account_hash = result.get("account_hash")
    if type(estimate) is not int or estimate < 0:
        raise TypeError("recent_message_estimate is invalid")
    if not isinstance(history_id, str) or not history_id:
        raise TypeError("history_id is invalid")
    if not isinstance(watch_expiration, str) or not watch_expiration.isdigit():
        raise TypeError("watch_expiration is invalid")
    if (
        not isinstance(account_hash, str)
        or len(account_hash) != 64
        or any(character not in "0123456789abcdef" for character in account_hash)
    ):
        raise TypeError("account_hash is invalid")
    return {
        "recent_message_estimate": estimate,
        "history_id": history_id,
        "watch_expiration": watch_expiration,
        "account_hash": account_hash,
    }


def _scan_page_details(
    result: Mapping[str, object], *, interest_results: bool = False
) -> dict[str, Any]:
    if result.get("status") not in {"CONNECTED", "SCAN_PAGE"}:
        raise RuntimeError("AgentCore did not return a Gmail scan page")
    processed = _non_negative_integer(
        result.get("processed_message_count"),
        field_name="processed_message_count",
    )
    if processed > 8:
        raise TypeError("processed_message_count exceeds one scan page")
    estimate = _non_negative_integer(
        result.get("recent_message_estimate"),
        field_name="recent_message_estimate",
    )
    next_page_token_value = result.get("next_page_token")
    next_page_token = (
        None if next_page_token_value is None else _page_token(next_page_token_value)
    )
    completion_history_value = result.get("completion_history_id")
    if next_page_token is None:
        completion_history_id = _history_id(completion_history_value)
    elif completion_history_value is not None:
        raise TypeError("non-final Gmail scan page has a completion history ID")
    else:
        completion_history_id = None
    signal = _signal_records(result, interest_results=interest_results)
    if processed != len(signal["evidence"]):
        raise TypeError("Gmail scan page count does not match its evidence")
    return {
        "processed_message_count": processed,
        "recent_message_estimate": estimate,
        "next_page_token": next_page_token,
        "completion_history_id": completion_history_id,
        **signal,
    }


def _history_id(value: object) -> str:
    if not isinstance(value, str) or not value.isdigit() or len(value) > 64:
        raise TypeError("Gmail history ID is invalid")
    return value


def _sync_details(
    result: Mapping[str, object], *, interest_results: bool = False
) -> dict[str, Any]:
    if result.get("status") != "SYNCED":
        raise RuntimeError("AgentCore did not complete Gmail history sync")
    history_id = _history_id(result.get("history_id"))
    recovery_mode = result.get("recovery_mode")
    if recovery_mode not in {"INCREMENTAL", "BOUNDED_FULL_SYNC"}:
        raise TypeError("Gmail recovery mode is invalid")
    continuation_required = result.get("continuation_required")
    if type(continuation_required) is not bool:
        raise TypeError("Gmail continuation flag is invalid")
    signal = _signal_records(result, interest_results=interest_results)
    if not interest_results and signal["unresolved_evidence_count"] != 0:
        raise RuntimeError("Gmail history batch contains unresolved evidence")
    return {
        "history_id": history_id,
        "recovery_mode": recovery_mode,
        "continuation_required": continuation_required,
        **signal,
    }


def _signal_records(
    result: Mapping[str, object], *, interest_results: bool = False
) -> dict[str, Any]:
    discovery_validated = result.get("discovery_validated") is True
    if interest_results and result.get("interest_validated") is not True:
        raise RuntimeError("Mail interest result was not safely validated")
    if not discovery_validated and not interest_results:
        raise RuntimeError("Gmail discovery result was not safely validated")
    evidence = result.get("evidence")
    if not isinstance(evidence, list) or len(evidence) > 8:
        raise TypeError("Gmail evidence batch is invalid")
    normalized_evidence = [_evidence_record(value) for value in evidence]
    unresolved = _non_negative_integer(
        result.get("unresolved_evidence_count"),
        field_name="unresolved_evidence_count",
    )
    if unresolved > len(normalized_evidence):
        raise TypeError("unresolved_evidence_count exceeds the evidence batch")
    candidate_values = result.get("candidates")
    if candidate_values is None:
        legacy_candidate = result.get("candidate")
        candidate_values = [] if legacy_candidate is None else [legacy_candidate]
    if not discovery_validated:
        candidate_values = []
    if not isinstance(candidate_values, list) or len(candidate_values) > 8:
        raise TypeError("Gmail candidate batch is invalid")
    candidates = [_candidate_record(value) for value in candidate_values]
    if candidates and not normalized_evidence:
        raise TypeError("Gmail candidates have no evidence batch")
    evidence_refs = {str(value["ref"]) for value in normalized_evidence}
    used_refs: set[str] = set()
    for candidate in candidates:
        candidate_refs = set(candidate["evidence_refs"])
        if (
            not candidate_refs
            or not candidate_refs.issubset(evidence_refs)
            or used_refs.intersection(candidate_refs)
        ):
            raise TypeError("Gmail candidate evidence references do not match")
        used_refs.update(candidate_refs)
    return {
        "evidence": normalized_evidence,
        "candidates": candidates,
        "unresolved_evidence_count": unresolved,
        **(
            {"action_preparation_incomplete": not discovery_validated or unresolved > 0}
            if interest_results
            else {}
        ),
    }


def _scan_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise TypeError("Gmail scan ID is invalid")
    return value


def _page_token(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(character.isspace() for character in value)
    ):
        raise TypeError("Gmail scan page token is invalid")
    return value


def _page_token_hash(page_token: str) -> str:
    return hashlib.sha256(page_token.encode("utf-8")).hexdigest()


def _scan_page_number(value: object, *, already_processed: int) -> int:
    if value is None:
        # Compatibility with one in-flight continuation created before page
        # lineage was added. New messages always carry the exact page number.
        return already_processed // 8 + 1
    if type(value) is not int or not 2 <= value <= MAX_INITIAL_SCAN_PAGES:
        raise TypeError("Gmail scan page number is invalid")
    return value


def _page_token_hashes(
    value: object,
    *,
    current_page_token: str,
) -> list[str]:
    current_hash = _page_token_hash(current_page_token)
    if value is None:
        return [current_hash]
    if not isinstance(value, list) or not value or len(value) > MAX_INITIAL_SCAN_PAGES:
        raise TypeError("Gmail scan page token lineage is invalid")
    hashes: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or len(item) != 64
            or any(character not in "0123456789abcdef" for character in item)
        ):
            raise TypeError("Gmail scan page token lineage is invalid")
        hashes.append(item)
    if len(hashes) != len(set(hashes)) or hashes[-1] != current_hash:
        raise ValueError("Gmail scan page token lineage does not match")
    return hashes


def _non_negative_integer(value: object, *, field_name: str) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise TypeError(f"{field_name} is invalid")
    return value


def _evidence_record(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "ref",
        "revision",
        "source",
        "title",
        "facts",
        "untrusted_text",
    }:
        raise TypeError("Gmail evidence record fields are invalid")
    ref = value.get("ref")
    title = value.get("title")
    facts = value.get("facts")
    if (
        not isinstance(ref, str)
        or not ref.startswith("gmail:")
        or len(ref) != 70
        or type(value.get("revision")) is not int
        or value.get("revision") != 1
        or value.get("source") != "gmail"
        or not isinstance(title, str)
        or not title
        or len(title) > 200
        or not isinstance(facts, list)
        or len(facts) > 12
        or any(not isinstance(fact, str) or len(fact) > 500 for fact in facts)
        or value.get("untrusted_text") is not None
    ):
        raise TypeError("Gmail evidence record is invalid")
    return dict(value)


def _candidate_record(value: object) -> dict[str, object]:
    required = {
        "outcome",
        "summary",
        "why_now",
        "opportunity_type",
        "evidence_refs",
        "confidence",
        "uncertainty_reason",
        "primary_group_hint",
        "tags",
        "risk",
        "required_capabilities",
        "proposed_actions",
        "fingerprint_inputs",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise TypeError("Gmail candidate fields are invalid")
    if (
        not isinstance(value.get("outcome"), str)
        or not isinstance(value.get("summary"), str)
        or not isinstance(value.get("why_now"), str)
        or value.get("opportunity_type") not in {"APPOINTMENT", "DEADLINE", "FOLLOW_UP"}
        or not isinstance(value.get("evidence_refs"), list)
        or not isinstance(value.get("confidence"), float)
        or not 0.7 <= value["confidence"] <= 1
        or value.get("risk") not in {"LOW", "MEDIUM", "HIGH"}
        or not isinstance(value.get("tags"), list)
        or not isinstance(value.get("required_capabilities"), list)
        or not isinstance(value.get("proposed_actions"), list)
        or not 1 <= len(value["proposed_actions"]) <= 3
        or any(not isinstance(action, Mapping) for action in value["proposed_actions"])
        or not isinstance(value.get("fingerprint_inputs"), list)
    ):
        raise TypeError("Gmail candidate is invalid")
    return dict(value)


# Keep the established public entry points available after extraction.
__all__ = [
    "ACTION_READY_DISCOVERY_REVISION",
    "GMAIL_READONLY_SCOPE",
    "HISTORY_SYNC_LEASE_SECONDS",
    "MAX_INITIAL_SCAN_PAGES",
    "AgentRuntime",
    "BotoAgentRuntime",
    "ConnectionWriter",
    "ContinuationQueue",
    "DynamoConnectionWriter",
    "GoogleJobProcessor",
    "HistorySyncClaim",
    "HistorySyncInProgress",
    "SqsContinuationQueue",
    "default_google_job_processor",
]
