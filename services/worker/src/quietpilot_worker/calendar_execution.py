"""Execute one immutable Calendar approval with transactional authorization and leases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .google_jobs import BotoAgentRuntime

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
LEASE_SECONDS = 150
_HASH = re.compile(r"[0-9a-f]{64}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)
_ACTIVE = {"QUEUED", "RUNNING", "VERIFYING"}
_ACTION_FIELDS = (
    "connector",
    "target",
    "verb",
    "parameters",
    "required_scopes",
    "risk",
    "reversible",
)


_RESULT_ERRORS = {
    "GOOGLE_AUTH_REQUIRED",
    "GOOGLE_ACCOUNT_CHANGED",
    "INVALID_CALENDAR_PARAMETERS",
    "CALENDAR_RESULT_UNCONFIRMED",
    "CALENDAR_READBACK_MISMATCH",
    "CALENDAR_EVENT_CANCELLED",
    "GOOGLE_CALENDAR_FORBIDDEN",
    "CALENDAR_EVENT_GONE",
    "GOOGLE_CALENDAR_UNAVAILABLE",
    "GOOGLE_CALENDAR_REQUEST_REJECTED",
}


class CalendarExecutionRetry(RuntimeError):
    """Keep the SQS record available; a previous write may already exist."""


class CalendarExecutionLeaseLost(CalendarExecutionRetry):
    pass


class _Blocked(ValueError):
    def __init__(self, code: str, status: str = "DECISION_REQUIRED") -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class CalendarRuntime(Protocol):
    def invoke_payload(
        self, user_id: str, payload: Mapping[str, object]
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class _Snapshot:
    user_id: str
    case_id: str
    operation_id: str
    meta: dict[str, Any]
    approval: dict[str, Any]
    plan: dict[str, Any]
    evidence: list[dict[str, Any]]
    connections: list[dict[str, Any]]
    execution: dict[str, Any]


@dataclass(frozen=True)
class _Claim:
    snapshot: _Snapshot
    action: dict[str, Any]
    record: dict[str, Any]
    case_version: int
    token: str


@dataclass(frozen=True)
class CalendarExecutionProcessor:
    runtime: CalendarRuntime
    store: DynamoCalendarExecutionStore

    def process(self, envelope: Mapping[str, object]) -> None:
        if (
            envelope.get("event_type") != "ACTION_EXECUTE_REQUESTED"
            or envelope.get("connector") != "google"
        ):
            raise ValueError("Calendar execution event is invalid")
        user_id = _text(envelope.get("user_id"), 128)
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {
            "case_id",
            "operation_id",
        }:
            raise ValueError("Calendar execution payload is invalid")
        case_id = _text(payload.get("case_id"), 128)
        operation_id = _text(payload.get("operation_id"), 64)
        if _HASH.fullmatch(operation_id) is None:
            raise ValueError("Calendar operation identity is invalid")
        claim = self.store.claim(user_id, case_id, operation_id)
        if claim is None:
            return
        invocation = {
            "operation": "GOOGLE_CALENDAR_EXECUTE",
            "user_id": user_id,
            "operation_id": operation_id,
            "account_hash": _s(claim.snapshot.approval, "account_hash"),
            "parameters": {
                key: value
                for key, value in claim.action["parameters"].items()
                if key != "source_ref"
            },
        }
        try:
            response = self.runtime.invoke_payload(user_id, invocation)
            result = _result(response, operation_id)
        except Exception:  # noqa: BLE001 - a transport failure may follow a committed write
            result = _result(None, operation_id)
        self.store.finish(claim, result)
        if result["status"] == "VERIFYING":
            raise CalendarExecutionRetry("Calendar result is not yet verified")


class DynamoCalendarExecutionStore:
    def __init__(
        self,
        table_name: str,
        client: Any,
        *,
        clock: Callable[[], float] = time.time,
        lease_seconds: int = LEASE_SECONDS,
        token_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        if not table_name or lease_seconds < LEASE_SECONDS:
            raise ValueError("Calendar execution store configuration is invalid")
        self.table = table_name
        self.client = client
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.token_factory = token_factory

    @classmethod
    def from_environment(cls) -> DynamoCalendarExecutionStore:
        import boto3

        table = os.environ.get("MAIN_TABLE_NAME", "").strip()
        return cls(table, boto3.client("dynamodb"))

    def claim(self, user_id: str, case_id: str, operation_id: str) -> _Claim | None:
        snapshot = self._load(user_id, case_id, operation_id)
        if snapshot is None or _s(snapshot.meta, "status") not in _ACTIVE:
            return None
        execution = snapshot.execution
        if execution and (
            _s(execution, "user_id") != user_id
            or _s(execution, "operation_id") != operation_id
        ):
            return None
        completed = _s(execution, "status") == "SUCCEEDED" and execution.get(
            "verified"
        ) == {"BOOL": True}
        now = int(self.clock())
        try:
            action = _validate(snapshot, now, completed=completed)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            blocked = (
                error
                if isinstance(error, _Blocked)
                else _Blocked("CALENDAR_APPROVAL_INVALID")
            )
            self._block(snapshot, blocked)
            return None
        if completed:
            self._promote_completed(snapshot)
            return None
        if _n(execution, "lease_until", 0) > now:
            raise CalendarExecutionRetry(
                "Calendar execution already has an active lease"
            )
        token = self.token_factory()
        timestamp = _timestamp(now)
        record = {
            **execution,
            **_key(case_id, f"ACTION#{operation_id}"),
            "entity_type": {"S": "action_execution"},
            "user_id": {"S": user_id},
            "operation_id": {"S": operation_id},
            "approval_id": snapshot.approval["approval_id"],
            "action_id": {"S": action["action_id"]},
            "plan_version": snapshot.approval["plan_version"],
            "plan_hash": snapshot.approval["plan_hash"],
            "status": {"S": "RUNNING"},
            "lease_token": {"S": token},
            "lease_until": {"N": str(now + self.lease_seconds)},
            "attempt_count": {"N": str(_n(execution, "attempt_count", 0) + 1)},
            "created_at": execution.get("created_at", {"S": timestamp}),
            "updated_at": {"S": timestamp},
            "result_summary": execution.get(
                "result_summary",
                {"S": "Saving the approved event and checking the result."},
            ),
            "result_ref": execution.get("result_ref", {"NULL": True}),
            "html_url": execution.get("html_url", {"NULL": True}),
            "verified": {"BOOL": False},
            "error_code": execution.get("error_code", {"NULL": True}),
        }
        put: dict[str, Any] = {"TableName": self.table, "Item": record}
        if execution:
            put.update(
                _same_fields(
                    execution,
                    ("user_id", "operation_id", "plan_hash", "status", "attempt_count"),
                )
            )
            put["ConditionExpression"] += (
                " AND (attribute_not_exists(lease_until) OR lease_until<=:lease_now)"
            )
            put["ExpressionAttributeValues"][":lease_now"] = {"N": str(now)}
        else:
            put["ConditionExpression"] = "attribute_not_exists(PK)"
        version = _n(snapshot.meta, "version")
        operations = self._authorization_checks(snapshot)
        operations.extend(
            [
                {
                    "Update": self._meta_update(
                        snapshot,
                        "RUNNING",
                        "Saving the approved event and checking the result.",
                        version,
                    )
                },
                {"Put": put},
            ]
        )
        try:
            self.client.transact_write_items(TransactItems=operations)
        except Exception as error:
            if _conditional(error):
                raise CalendarExecutionRetry(
                    "Calendar approval or lease changed before execution"
                ) from None
            raise
        return _Claim(snapshot, action, record, version + 1, token)

    def finish(self, claim: _Claim, result: dict[str, object]) -> None:
        from .notifications import notification_put

        snapshot = claim.snapshot
        result_status = str(result["status"])
        status = "SUCCEEDED" if result_status == "COMPLETED" else result_status
        summary = {
            "COMPLETED": "Verified that the approved event was saved to Calendar.",
            "VERIFYING": "The result is unconfirmed. Checking whether the same event was saved.",
            "FAILED": "Could not save the event. Check the error and connection.",
        }[result_status]
        record = {
            **claim.record,
            "status": {"S": status},
            "result_summary": {"S": summary},
            "verified": {"BOOL": result["verified"] is True},
            "updated_at": {"S": _timestamp(self.clock())},
            "lease_until": claim.record["lease_until"]
            if status == "VERIFYING"
            else {"N": str(int(self.clock()))},
        }
        record.pop("lease_token", None)
        for field in ("result_ref", "html_url", "error_code"):
            value = result[field]
            if field == "result_ref" and value is None and status == "VERIFYING":
                continue
            record[field] = {"S": value} if isinstance(value, str) else {"NULL": True}
        put = {
            "TableName": self.table,
            "Item": record,
            "ConditionExpression": "lease_token=:token AND operation_id=:operation AND user_id=:user",
            "ExpressionAttributeValues": {
                ":token": {"S": claim.token},
                ":operation": {"S": snapshot.operation_id},
                ":user": {"S": snapshot.user_id},
            },
        }
        case_status = (
            "PERMISSION_REVOKED"
            if result["error_code"]
            in {"GOOGLE_AUTH_REQUIRED", "GOOGLE_ACCOUNT_CHANGED"}
            else result_status
        )
        notification = (
            [
                notification_put(
                    self.table,
                    snapshot.user_id,
                    snapshot.case_id,
                    claim.case_version + 1,
                    "ACTION_FAILED",
                    self.clock(),
                )
            ]
            if case_status in {"FAILED", "PERMISSION_REVOKED"}
            else []
        )
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {"Put": put},
                    {
                        "Update": self._meta_update(
                            snapshot,
                            case_status,
                            summary,
                            claim.case_version,
                            expected_status="RUNNING",
                        )
                    },
                    {
                        "Put": {
                            "TableName": self.table,
                            "Item": self._timeline(
                                snapshot.case_id, status, summary, claim.token
                            ),
                        }
                    },
                    *notification,
                ]
            )
        except Exception as error:
            if not _conditional(error):
                raise
            # Stop/new-plan decisions win. Preserve the observation without rewriting their Case.
            try:
                self.client.transact_write_items(
                    TransactItems=[
                        {"Put": put},
                        {
                            "Put": {
                                "TableName": self.table,
                                "Item": self._timeline(
                                    snapshot.case_id, status, summary, claim.token
                                ),
                            }
                        },
                    ]
                )
            except Exception as lease_error:
                if _conditional(lease_error):
                    raise CalendarExecutionLeaseLost(
                        "Calendar execution lease was replaced"
                    ) from None
                raise

    def _promote_completed(self, snapshot: _Snapshot) -> None:
        record = snapshot.execution
        saved = _result(
            {
                "status": "COMPLETED",
                "verified": record.get("verified", {}).get("BOOL"),
                **{
                    field: _s(record, field)
                    for field in ("result_ref", "html_url", "error_code")
                },
            },
            snapshot.operation_id,
        )
        if saved["verified"] is not True:
            self._block(snapshot, _Blocked("CALENDAR_RESULT_INVALID"))
            return
        operations = self._authorization_checks(snapshot)
        operations.extend(
            [
                {
                    "ConditionCheck": {
                        "TableName": self.table,
                        "Key": _key(
                            snapshot.case_id, f"ACTION#{snapshot.operation_id}"
                        ),
                        **_same_fields(
                            record,
                            (
                                "user_id",
                                "operation_id",
                                "plan_hash",
                                "plan_version",
                                "action_id",
                                "status",
                                "verified",
                                "result_ref",
                                "html_url",
                                "error_code",
                            ),
                        ),
                    }
                },
                {
                    "Update": self._meta_update(
                        snapshot,
                        "COMPLETED",
                        "Verified that this event was already saved.",
                        _n(snapshot.meta, "version"),
                    )
                },
            ]
        )
        try:
            self.client.transact_write_items(TransactItems=operations)
        except Exception as error:
            if _conditional(error):
                raise CalendarExecutionRetry(
                    "Calendar completion context changed"
                ) from None
            raise

    def _load(self, user_id: str, case_id: str, operation_id: str) -> _Snapshot | None:
        response = self.client.query(
            TableName=self.table,
            KeyConditionExpression="PK=:pk",
            ExpressionAttributeValues={":pk": {"S": f"CASE#{case_id}"}},
            ConsistentRead=True,
            Limit=101,
        )
        items = response.get("Items", [])
        if response.get("LastEvaluatedKey") or len(items) > 100:
            raise CalendarExecutionRetry("Calendar Case exceeds its bounded read")
        by_key = {_s(item, "SK"): item for item in items}
        meta = by_key.get("META", {})
        approval_id = _s(meta, "approved_approval_id")
        approval = by_key.get(f"APPROVAL#{approval_id}", {})
        if (
            _s(meta, "user_id") != user_id
            or _s(meta, "approved_operation_id") != operation_id
            or _s(approval, "user_id") != user_id
            or _s(approval, "operation_id") != operation_id
            or not approval_id
            or _s(approval, "approval_id") != approval_id
        ):
            return None
        version = _n(approval, "plan_version", 0)
        connections = [
            self.client.get_item(
                TableName=self.table,
                Key={"PK": {"S": f"USER#{user_id}"}, "SK": {"S": name}},
                ConsistentRead=True,
            ).get("Item", {})
            for name in ("CONNECTION#google", "CONNECTION#google-calendar")
        ]
        return _Snapshot(
            user_id,
            case_id,
            operation_id,
            meta,
            approval,
            by_key.get(f"PLAN#{version:06d}", {}),
            [item for item in items if _s(item, "entity_type") == "case_evidence"],
            connections,
            by_key.get(f"ACTION#{operation_id}", {}),
        )

    def _authorization_checks(self, snapshot: _Snapshot) -> list[dict[str, Any]]:
        records = [
            (
                snapshot.approval,
                (
                    "user_id",
                    "operation_id",
                    "approval_id",
                    "plan_hash",
                    "plan_version",
                    "expected_case_version",
                    "grant_mode",
                    "decision",
                    "expires_at",
                    "action_json",
                    "mail_connection_id",
                    "account_hash",
                ),
            ),
            (snapshot.plan, ("plan_hash", "plan_json", "version")),
        ]
        records.extend(
            (item, ("user_id", "evidence_ref", "revision"))
            for item in snapshot.evidence
        )
        records.extend(
            (item, ("status", "mail_connection_id", "account_hash", "granted_scopes"))
            for item in snapshot.connections
        )
        return [
            {
                "ConditionCheck": {
                    "TableName": self.table,
                    "Key": {"PK": item["PK"], "SK": item["SK"]},
                    **_same_fields(item, fields),
                }
            }
            for item, fields in records
        ]

    def _meta_update(
        self,
        snapshot: _Snapshot,
        status: str,
        message: str,
        version: int,
        *,
        expected_status: str | None = None,
    ) -> dict[str, Any]:
        guard = _same_fields(
            snapshot.meta,
            (
                "user_id",
                "approved_operation_id",
                "approved_approval_id",
                "current_plan_hash",
                "current_plan_version",
                "requested_plan_version",
                "evidence_refs",
            ),
        )
        guard["ConditionExpression"] += (
            " AND #case_version=:case_version AND #case_status=:case_status"
        )
        guard["ExpressionAttributeNames"].update(
            {"#case_version": "version", "#case_status": "status"}
        )
        guard["ExpressionAttributeValues"].update(
            {
                ":case_version": {"N": str(version)},
                ":case_status": {"S": expected_status or _s(snapshot.meta, "status")},
            }
        )
        timestamp = _timestamp(self.clock())
        bucket = "HISTORY" if status == "COMPLETED" else "ACTIVE"
        guard["ExpressionAttributeValues"].update(
            {
                ":next_status": {"S": status},
                ":message": {"S": message},
                ":now": {"S": timestamp},
                ":one": {"N": "1"},
                ":gsi_pk": {"S": f"USER#{snapshot.user_id}#CASE#{bucket}"},
                ":gsi_sk": {"S": f"999949#{timestamp}#{snapshot.case_id}"},
            }
        )
        return {
            "TableName": self.table,
            "Key": _key(snapshot.case_id, "META"),
            **guard,
            "UpdateExpression": "SET #case_status=:next_status,next_action=:message,updated_at=:now,#case_version=#case_version+:one,GSI1PK=:gsi_pk,GSI1SK=:gsi_sk",
        }

    def _block(self, snapshot: _Snapshot, blocked: _Blocked) -> None:
        from .notifications import notification_put

        # An expired/revoked approval cannot authorize even a readback. Unknown results remain intact.
        message = (
            "Connection permissions changed. Execution stopped; check whether the earlier request took effect."
            if blocked.status == "PERMISSION_REVOKED"
            else "The plan or approval changed. Review it before approving again."
        )
        version = _n(snapshot.meta, "version")
        material_changed = (
            blocked.code in {"CALENDAR_PLAN_CHANGED", "CALENDAR_EVIDENCE_CHANGED"}
            or _s(snapshot.meta, "current_plan_hash")
            != _s(snapshot.approval, "plan_hash")
            or _n(snapshot.meta, "current_plan_version", 0)
            != _n(snapshot.approval, "plan_version", 0)
            or _n(snapshot.meta, "requested_plan_version", 0)
            != _n(snapshot.approval, "plan_version", 0)
        )
        kind = (
            "ACTION_FAILED"
            if blocked.status == "PERMISSION_REVOKED"
            else "PLAN_CHANGED"
            if material_changed
            else "DECISION_REQUIRED"
        )
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Update": self._meta_update(
                            snapshot, blocked.status, message, version
                        )
                    },
                    notification_put(
                        self.table,
                        snapshot.user_id,
                        snapshot.case_id,
                        version + 1,
                        kind,
                        self.clock(),
                    ),
                ]
            )
        except Exception as error:
            if not _conditional(error):
                raise

    def _timeline(
        self, case_id: str, status: str, message: str, token: str
    ) -> dict[str, Any]:
        timestamp = _timestamp(self.clock())
        return {
            **_key(case_id, f"EVENT#{timestamp}#{token}"),
            "entity_type": {"S": "audit_event"},
            "event_id": {"S": token},
            "label": {"S": "Calendar result"},
            "body": {"S": message},
            "state": {"S": "DONE" if status == "SUCCEEDED" else "CURRENT"},
            "occurred_at": {"S": timestamp},
        }


def _validate(
    snapshot: _Snapshot, now: int, *, completed: bool = False
) -> dict[str, Any]:
    meta, approval = snapshot.meta, snapshot.approval
    plan_version, expected_version = (
        _n(approval, "plan_version"),
        _n(approval, "expected_case_version"),
    )
    plan_hash = _s(approval, "plan_hash")
    if (
        _s(approval, "grant_mode") != "ONCE"
        or _s(approval, "decision") != "APPROVE"
        or plan_version < 1
        or expected_version < 1
        or not _HASH.fullmatch(plan_hash or "")
        or _s(meta, "current_plan_hash") != plan_hash
        or _n(meta, "current_plan_version") != plan_version
        or _n(meta, "requested_plan_version") != plan_version
        or _n(meta, "version") < expected_version + 1
    ):
        raise _Blocked("CALENDAR_APPROVAL_CHANGED")
    identity = hashlib.sha256(
        f"{snapshot.user_id}:{snapshot.case_id}:{plan_version}:{plan_hash}".encode()
    ).hexdigest()
    if identity != snapshot.operation_id:
        raise _Blocked("CALENDAR_APPROVAL_INVALID")
    approval_id = hashlib.sha256(f"{identity}:{expected_version}".encode()).hexdigest()
    if (
        _s(approval, "approval_id") != approval_id
        or _s(meta, "approved_approval_id") != approval_id
    ):
        raise _Blocked("CALENDAR_APPROVAL_CHANGED")
    expires = datetime.fromisoformat(_s(approval, "expires_at") or "")
    if expires.tzinfo is None or expires.timestamp() <= now:
        raise _Blocked("CALENDAR_APPROVAL_EXPIRED")
    epoch, account = _s(approval, "mail_connection_id"), _s(approval, "account_hash")
    if not epoch or not _HASH.fullmatch(account or ""):
        raise _Blocked("CALENDAR_ACCOUNT_CHANGED", "PERMISSION_REVOKED")
    for connection, scope in zip(
        snapshot.connections, (GMAIL_SCOPE, CALENDAR_SCOPE), strict=True
    ):
        if (
            _s(connection, "status") != "CONNECTED"
            or _s(connection, "mail_connection_id") != epoch
            or _s(connection, "account_hash") != account
            or scope not in _strings(connection, "granted_scopes")
            or _s(connection, "user_id") not in {None, snapshot.user_id}
        ):
            raise _Blocked("CALENDAR_PERMISSION_CHANGED", "PERMISSION_REVOKED")
    plan = json.loads(_s(snapshot.plan, "plan_json") or "null")
    action = json.loads(_s(approval, "action_json") or "null")
    expected_revisions = {}
    for evidence in snapshot.evidence:
        reference = _s(evidence, "evidence_ref")
        revision = _n(evidence, "revision")
        if (
            not reference
            or reference in expected_revisions
            or revision < 1
            or _s(evidence, "user_id") != snapshot.user_id
        ):
            raise _Blocked("CALENDAR_EVIDENCE_CHANGED")
        expected_revisions[reference] = revision
    if (
        not 1 <= len(expected_revisions) <= 8
        or set(_strings(meta, "evidence_refs")) != set(expected_revisions)
        or not isinstance(plan, dict)
        or not isinstance(action, dict)
        or plan.get("actions") != [action]
        or plan.get("evidence_revisions") != expected_revisions
        or plan.get("version") != plan_version
        or plan.get("hash") != plan_hash
        or _s(snapshot.plan, "plan_hash") != plan_hash
        or _n(snapshot.plan, "version") != plan_version
        or action.get("connector") != "google"
        or action.get("verb") != "calendar_event_create"
        or action.get("target") != "primary"
        or action.get("risk") not in {"MEDIUM", "HIGH"}
        or action.get("risk") != plan.get("risk")
        or action.get("risk") != _s(meta, "risk")
        or action.get("reversible") is not True
        or action.get("required_scopes") != [CALENDAR_SCOPE]
        or plan.get("required_scopes") != [CALENDAR_SCOPE]
    ):
        raise _Blocked("CALENDAR_PLAN_CHANGED")
    _text(action.get("action_id"), 128)
    material = {
        "case_id": snapshot.case_id,
        "version": plan_version,
        "evidence_revisions": expected_revisions,
        "actions": [{key: action[key] for key in _ACTION_FIELDS}],
        "risk": plan["risk"],
    }
    if _digest(material) != plan_hash:
        raise _Blocked("CALENDAR_PLAN_CHANGED")
    _validate_parameters(
        action.get("parameters"), expected_revisions, now, require_future=not completed
    )
    if snapshot.execution and (
        _s(snapshot.execution, "plan_hash") != plan_hash
        or _n(snapshot.execution, "plan_version") != plan_version
        or _s(snapshot.execution, "action_id") != action["action_id"]
    ):
        raise _Blocked("CALENDAR_EXECUTION_CHANGED")
    return action


def _validate_parameters(
    value: object, evidence: Mapping[str, int], now: int, *, require_future: bool = True
) -> None:
    required = {"summary", "start", "end", "source_ref"}
    if (
        not isinstance(value, Mapping)
        or not required <= value.keys()
        or value.keys() - required - {"description", "timeZone"}
    ):
        raise _Blocked("CALENDAR_PARAMETERS_INVALID")
    for key, text in value.items():
        limit = {
            "summary": 1000,
            "description": 2000,
            "start": 40,
            "end": 40,
            "timeZone": 128,
            "source_ref": 256,
        }[key]
        if (
            not isinstance(text, str)
            or len(text) > limit
            or any(
                (ord(c) < 32 and not (key == "description" and c in "\n\t"))
                or ord(c) == 127
                or 0xD800 <= ord(c) <= 0xDFFF
                for c in text
            )
        ):
            raise _Blocked("CALENDAR_PARAMETERS_INVALID")
    if not value["summary"].strip() or value["source_ref"] not in evidence:
        raise _Blocked("CALENDAR_PARAMETERS_INVALID")
    start, end = value["start"], value["end"]
    if _DATE.fullmatch(start) and _DATE.fullmatch(end):
        if "timeZone" in value or date.fromisoformat(end) <= date.fromisoformat(start):
            raise _Blocked("CALENDAR_PARAMETERS_INVALID")
        end_time = datetime.fromisoformat(end).replace(tzinfo=UTC)
    else:
        if any(
            not _TIME.fullmatch(item) or item.endswith("-00:00")
            for item in (start, end)
        ):
            raise _Blocked("CALENDAR_PARAMETERS_INVALID")
        start_time, end_time = (
            datetime.fromisoformat(start.upper()),
            datetime.fromisoformat(end.upper()),
        )
        if end_time <= start_time:
            raise _Blocked("CALENDAR_PARAMETERS_INVALID")
        if "timeZone" in value:
            try:
                zone = ZoneInfo(value["timeZone"])
            except (ValueError, ZoneInfoNotFoundError):
                raise _Blocked("CALENDAR_PARAMETERS_INVALID") from None
            if any(
                item.astimezone(zone).utcoffset() != item.utcoffset()
                for item in (start_time, end_time)
            ):
                raise _Blocked("CALENDAR_PARAMETERS_INVALID")
    if require_future and end_time.timestamp() <= now:
        raise _Blocked("CALENDAR_EVENT_TIME_PASSED")


def _result(value: object, operation_id: str) -> dict[str, object]:
    unknown: dict[str, object] = {
        "status": "VERIFYING",
        "verified": False,
        "result_ref": None,
        "html_url": None,
        "error_code": "CALENDAR_RESULT_UNCONFIRMED",
    }
    if (
        not isinstance(value, Mapping)
        or value.get("status") not in {"COMPLETED", "VERIFYING", "FAILED"}
        or type(value.get("verified")) is not bool
    ):
        return unknown
    status, verified, reference = (
        value["status"],
        value["verified"],
        value.get("result_ref"),
    )
    if (status == "COMPLETED") != verified or (
        verified and not isinstance(reference, str)
    ):
        return unknown
    expected_reference = "google-calendar:primary:qp" + _digest(
        {"operation": "calendar.event_create.v1", "id": operation_id}
    )
    if reference is not None and reference != expected_reference:
        return unknown
    code = value.get("error_code")
    if (
        code is not None and (not isinstance(code, str) or code not in _RESULT_ERRORS)
    ) or (verified and code is not None):
        return unknown
    url = value.get("html_url")
    if url is not None and (not verified or not _safe_url(url)):
        return unknown
    return {
        "status": status,
        "verified": verified,
        "result_ref": reference,
        "html_url": url,
        "error_code": code,
    }


def _safe_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or any(ord(c) <= 32 for c in value)
    ):
        return False
    try:
        parts = urlsplit(value)
        query = parse_qs(parts.query)
        return (
            parts.scheme == "https"
            and parts.netloc in {"www.google.com", "calendar.google.com"}
            and parts.path == "/calendar/event"
            and not parts.fragment
            and set(query) <= {"eid", "ctz"}
            and len(query.get("eid", [])) == 1
        )
    except ValueError:
        return False


def default_calendar_execution_processor() -> CalendarExecutionProcessor:
    return CalendarExecutionProcessor(
        BotoAgentRuntime.from_environment(),
        DynamoCalendarExecutionStore.from_environment(),
    )


def _same_fields(item: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        "ConditionExpression": " AND ".join(
            f"#bound{i}=:bound{i}" for i in range(len(fields))
        ),
        "ExpressionAttributeNames": {
            f"#bound{i}": field for i, field in enumerate(fields)
        },
        "ExpressionAttributeValues": {
            f":bound{i}": item[field] for i, field in enumerate(fields)
        },
    }


def _key(case_id: str, suffix: str) -> dict[str, dict[str, str]]:
    return {"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": suffix}}


def _s(item: Mapping[str, Any], field: str) -> str | None:
    value = item.get(field, {}).get("S")
    return value if isinstance(value, str) else None


def _n(item: Mapping[str, Any], field: str, default: int | None = None) -> int:
    value = item.get(field, {}).get("N")
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    if default is not None:
        return default
    raise ValueError("Calendar record number is invalid")


def _strings(item: Mapping[str, Any], field: str) -> list[str]:
    values = item.get(field, {}).get("L")
    if not isinstance(values, list) or any(
        not isinstance(value, dict) or not isinstance(value.get("S"), str)
        for value in values
    ):
        raise ValueError("Calendar record list is invalid")
    return [value["S"] for value in values]


def _text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("Calendar record identity is invalid")
    return value


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _conditional(error: Exception) -> bool:
    return getattr(error, "response", {}).get("Error", {}).get("Code") in {
        "TransactionCanceledException",
        "ConditionalCheckFailedException",
    }
