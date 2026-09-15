"""Explicitly activated, source-bound mail preparation routines. No execution grants."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .calendar_connection import CALENDAR_SCOPE
from .mail import configured
from .workspace_records import _integer, _string, _string_list

MAX_ROUTINES = 32
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DEADLINE_DESCRIPTION = (
    "This 15-minute block marks the exact deadline, not the duration of an event."
)
LEGACY_DEADLINE_DESCRIPTION = (
    "정확한 마감 시각을 표시하는 15분 블록이에요. 실제 행사의 진행 시간이 아니에요."
)
REVIEW_REASONS = {
    "connection": "Google connection changed. Create a new routine for the current connection.",
    "profile": "Mail interests changed. Create a new routine for the current settings.",
    "source": "The original completed task changed. Create a new routine.",
}
_DOMAIN = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class RoutineInputError(ValueError):
    pass


class RoutineNotFound(LookupError):
    pass


class RoutineConflict(RuntimeError):
    pass


class RoutineNotReady(RuntimeError):
    pass


class RoutineLimitError(RuntimeError):
    pass


def _key(user_id: str, suffix: str) -> dict[str, Any]:
    return {"PK": {"S": f"USER#{user_id}"}, "SK": {"S": suffix}}


def _identity(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise RoutineInputError("Routine identity is invalid")
    return value


def _version(value: object) -> int:
    if type(value) is not int or value < 1:
        raise RoutineInputError("Routine version is invalid")
    return value


def _stamp(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RoutineInputError("Routine clock requires an explicit timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json(item: Mapping[str, Any], field: str) -> Any:
    try:
        return json.loads(_string(item, field) or "null")
    except (ValueError, TypeError):
        raise RoutineNotReady("Routine source metadata is invalid") from None


def _domain(item: Mapping[str, Any]) -> str:
    facts = _string_list(item, "facts")
    domains = [
        fact.removeprefix("sender_domain=").lower()
        for fact in facts
        if fact.startswith("sender_domain=")
    ]
    if len(domains) != 1 or _DOMAIN.fullmatch(domains[0]) is None:
        raise RoutineNotReady("Routine requires one valid source sender domain")
    return domains[0]


def _snapshot(
    table: str, item: Mapping[str, Any], fields: tuple[str, ...]
) -> dict[str, Any]:
    names, values, conditions = {}, {}, []
    for index, field in enumerate(fields):
        name, slot = f"#f{index}", f":v{index}"
        names[name] = field
        if field in item:
            conditions.append(f"{name}={slot}")
            values[slot] = item[field]
        else:
            conditions.append(f"attribute_not_exists({name})")
    return {
        "ConditionCheck": {
            "TableName": table,
            "Key": {name: item[name] for name in ("PK", "SK")},
            "ConditionExpression": " AND ".join(conditions),
            "ExpressionAttributeNames": names,
            **({"ExpressionAttributeValues": values} if values else {}),
        }
    }


class RoutineService:
    def __init__(
        self, store: Any, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ):
        self.store, self.clock = store, clock

    def propose(
        self, user_id: str, *, case_id: str, expected_version: int
    ) -> dict[str, Any]:
        item = self.store.propose(
            _identity(user_id),
            _identity(case_id),
            _version(expected_version),
            _stamp(self.clock),
        )
        return {"routine": self.store.public(item)}

    def list(self, user_id: str) -> dict[str, Any]:
        return {
            "routines": [
                self.store.public(item) for item in self.store.list(_identity(user_id))
            ]
        }

    def activate(
        self, user_id: str, *, routine_id: str, expected_version: int
    ) -> dict[str, Any]:
        item = self.store.set_status(
            _identity(user_id),
            _identity(routine_id),
            _version(expected_version),
            "ACTIVE",
            _stamp(self.clock),
        )
        return {"routine": self.store.public(item)}

    def pause(
        self, user_id: str, *, routine_id: str, expected_version: int
    ) -> dict[str, Any]:
        item = self.store.set_status(
            _identity(user_id),
            _identity(routine_id),
            _version(expected_version),
            "PAUSED",
            _stamp(self.clock),
        )
        return {"routine": self.store.public(item)}


class DynamoRoutineStore:
    def __init__(self, table_name: str, client: Any):
        self.table, self.client = table_name, client

    def _get(self, key: dict[str, Any]) -> dict[str, Any]:
        return (
            self.client.get_item(
                TableName=self.table, Key=key, ConsistentRead=True
            ).get("Item")
            or {}
        )

    def _current(
        self, user_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        connection = self._get(_key(user_id, "CONNECTION#google"))
        mail = self._get(_key(user_id, "MAIL_INTERESTS#google"))
        profile = _json(mail, "profile_json")
        if (
            _string(connection, "status") != "CONNECTED"
            or not _string(connection, "mail_connection_id")
            or not _HASH.fullmatch(_string(connection, "account_hash") or "")
            or GMAIL_SCOPE not in _string_list(connection, "granted_scopes")
        ):
            raise RoutineNotReady(REVIEW_REASONS["connection"])
        if (
            not isinstance(profile, dict)
            or not configured(profile)
            or type(profile.get("version")) is not int
            or profile["version"] < 1
            or _integer(mail, "profile_version") != profile["version"]
        ):
            raise RoutineNotReady(REVIEW_REASONS["profile"])
        return connection, mail, profile

    def _source(self, user_id: str, case_id: str, version: int):
        response = self.client.query(
            TableName=self.table,
            KeyConditionExpression="PK=:pk",
            ExpressionAttributeValues={":pk": {"S": f"CASE#{case_id}"}},
            ConsistentRead=True,
            Limit=101,
        )
        items = response.get("Items", [])
        if len(items) > 100 or response.get("LastEvaluatedKey"):
            raise RoutineLimitError("Routine source Case exceeds its read limit")
        by_key = {_string(item, "SK"): item for item in items}
        meta = by_key.get("META", {})
        if _string(meta, "user_id") != user_id:
            raise RoutineNotFound("Routine source Case not found")
        if _integer(meta, "version") != version:
            raise RoutineConflict("Routine source Case version changed")
        if _string(meta, "status") != "COMPLETED":
            raise RoutineNotReady("Routine requires a completed verified Calendar Case")
        plan_version, plan_hash = (
            _integer(meta, "current_plan_version"),
            _string(meta, "current_plan_hash"),
        )
        operation, approval_id = (
            _string(meta, "approved_operation_id"),
            _string(meta, "approved_approval_id"),
        )
        if (
            not plan_version
            or not _HASH.fullmatch(plan_hash or "")
            or not operation
            or not approval_id
        ):
            raise RoutineNotReady("Routine source approval is incomplete")
        plan_item = by_key.get(f"PLAN#{plan_version:06d}", {})
        plan = _json(plan_item, "plan_json")
        execution = by_key.get(f"ACTION#{operation}", {})
        approval = by_key.get(f"APPROVAL#{approval_id}", {})
        evidence = [
            item for item in items if _string(item, "entity_type") == "case_evidence"
        ]
        if (
            not isinstance(plan, dict)
            or not isinstance(plan.get("actions"), list)
            or len(plan["actions"]) != 1
        ):
            raise RoutineNotReady("Routine requires one verified Calendar action")
        action = plan["actions"][0]
        revisions = {
            _string(item, "evidence_ref"): _integer(item, "revision")
            for item in evidence
        }
        if (
            not evidence
            or len(evidence) > 8
            or len(revisions) != len(evidence)
            or any(
                _string(item, "user_id") != user_id or not _integer(item, "revision")
                for item in evidence
            )
            or not isinstance(plan.get("evidence_revisions"), dict)
            or any(
                type(value) is not int or value < 1
                for value in plan["evidence_revisions"].values()
            )
            or set(_string_list(meta, "evidence_refs")) != set(revisions)
            or plan.get("evidence_revisions") != revisions
            or type(plan.get("version")) is not int
            or plan.get("version") != plan_version
            or plan.get("hash") != plan_hash
        ):
            raise RoutineNotReady("Routine source evidence is invalid")
        params = action.get("parameters") if isinstance(action, dict) else None
        if (
            not isinstance(params, dict)
            or action.get("connector") != "google"
            or action.get("verb") != "calendar_event_create"
            or action.get("target") != "primary"
            or action.get("required_scopes") != [CALENDAR_SCOPE]
            or action.get("risk") not in {"MEDIUM", "HIGH"}
            or action.get("reversible") is not True
            or plan.get("risk") != action.get("risk")
            or plan.get("required_scopes") != [CALENDAR_SCOPE]
        ):
            raise RoutineNotReady("Routine source Calendar action is invalid")
        try:
            material = {
                "case_id": case_id,
                "version": plan_version,
                "evidence_revisions": revisions,
                "actions": [
                    {
                        field: action[field]
                        for field in (
                            "connector",
                            "target",
                            "verb",
                            "parameters",
                            "required_scopes",
                            "risk",
                            "reversible",
                        )
                    }
                ],
                "risk": plan["risk"],
            }
            digest = hashlib.sha256(
                json.dumps(
                    material,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            if digest != plan_hash:
                raise ValueError
            if (
                not {"summary", "start", "end", "source_ref"} <= params.keys()
                or params.keys()
                - {"summary", "start", "end", "source_ref", "description", "timeZone"}
                or not all(isinstance(value, str) for value in params.values())
            ):
                raise ValueError
            all_day = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", params["start"]))
            if all_day != bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", params["end"])):
                raise ValueError
            start = (
                date.fromisoformat(params["start"])
                if all_day
                else datetime.fromisoformat(params["start"])
            )
            end = (
                date.fromisoformat(params["end"])
                if all_day
                else datetime.fromisoformat(params["end"])
            )
            if not all_day and (start.tzinfo is None or end.tzinfo is None):
                raise ValueError
            if not timedelta(0) < end - start <= timedelta(days=31):
                raise ValueError
            deadline = params.get("description") in {
                DEADLINE_DESCRIPTION,
                LEGACY_DEADLINE_DESCRIPTION,
            }
            if deadline and (all_day or end - start != timedelta(minutes=15)):
                raise ValueError
        except (KeyError, ValueError, TypeError, OverflowError):
            raise RoutineNotReady(
                "Routine source Calendar material is invalid"
            ) from None
        source = next(
            (
                item
                for item in evidence
                if _string(item, "evidence_ref") == params["source_ref"]
            ),
            {},
        )
        if (
            _string(source, "source") != "gmail"
            or re.fullmatch(r"gmail:[0-9a-f]{64}", params["source_ref"]) is None
        ):
            raise RoutineNotReady("Routine requires owned Gmail evidence")
        result_ref = (
            "google-calendar:primary:qp"
            + hashlib.sha256(
                json.dumps(
                    {"operation": "calendar.event_create.v1", "id": operation},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        if (
            _string(execution, "user_id") != user_id
            or _string(execution, "status") != "SUCCEEDED"
            or execution.get("verified") != {"BOOL": True}
            or _string(execution, "result_ref") != result_ref
            or _string(execution, "action_id") != action.get("action_id")
            or _string(execution, "operation_id") != operation
            or _string(execution, "approval_id") != approval_id
            or _integer(execution, "plan_version") != plan_version
            or _string(execution, "plan_hash") != plan_hash
            or _string(approval, "user_id") != user_id
            or _string(approval, "operation_id") != operation
            or _string(approval, "decision") != "APPROVE"
            or _string(approval, "grant_mode") != "ONCE"
            or _integer(approval, "plan_version") != plan_version
            or _string(approval, "plan_hash") != plan_hash
        ):
            raise RoutineNotReady("Routine source result is not verified")
        connection, mail, profile = self._current(user_id)
        account, epoch = (
            _string(connection, "account_hash"),
            _string(connection, "mail_connection_id"),
        )
        source_epoch = _string(meta, "mail_connection_id")
        if (
            any(
                _string(
                    item, "account_hash" if item is approval else "google_account_hash"
                )
                != account
                for item in (approval, meta)
            )
            or not source_epoch
            or _string(approval, "mail_connection_id") != source_epoch
        ):
            raise RoutineNotReady(REVIEW_REASONS["connection"])
        binding = {
            "source_case_id": case_id,
            "source_case_version": version,
            "source_plan_version": plan_version,
            "source_plan_hash": plan_hash,
            "source_evidence_ref": params["source_ref"],
            "source_evidence_revision": _integer(source, "revision"),
            "source_evidence_sk": _string(source, "SK"),
            "source_execution_sk": _string(execution, "SK"),
            "source_approval_sk": _string(approval, "SK"),
            "sender_domain": _domain(source),
            "opportunity_type": "DEADLINE" if deadline else "APPOINTMENT",
            "connection_id": epoch,
            "source_connection_id": source_epoch,
            "account_hash": account,
            "profile_version": profile["version"],
        }
        guards = [
            _snapshot(
                self.table,
                item,
                tuple(field for field in item if field not in {"PK", "SK"}),
            )
            for item in (meta, plan_item, execution, approval, source)
        ]
        guards += [
            _snapshot(
                self.table,
                connection,
                ("status", "mail_connection_id", "account_hash", "granted_scopes"),
            ),
            _snapshot(self.table, mail, ("profile_version", "profile_json")),
        ]
        return binding, guards

    def list(self, user_id: str) -> list[dict[str, Any]]:
        response = self.client.query(
            TableName=self.table,
            KeyConditionExpression="PK=:pk AND begins_with(SK,:prefix)",
            ExpressionAttributeValues={
                ":pk": {"S": f"USER#{user_id}"},
                ":prefix": {"S": "ROUTINE#"},
            },
            ConsistentRead=True,
            Limit=MAX_ROUTINES + 1,
        )
        items = response.get("Items", [])
        if len(items) > MAX_ROUTINES or response.get("LastEvaluatedKey"):
            raise RoutineLimitError("Routine list exceeds the 32-routine view")
        if any(
            _string(item, "user_id") != user_id
            or _string(item, "mode") != "PREPARE_ONLY"
            for item in items
        ):
            raise RoutineNotReady("Routine storage scope is invalid")
        return sorted(
            items, key=lambda item: _string(item, "created_at") or "", reverse=True
        )

    def _transact(self, transactions):
        try:
            self.client.transact_write_items(TransactItems=transactions)
        except self.client.exceptions.TransactionCanceledException:
            raise RoutineConflict("Routine context changed") from None

    def propose(self, user_id: str, case_id: str, version: int, now: str):
        binding, guards = self._source(user_id, case_id, version)
        identity = [
            user_id,
            case_id,
            version,
            binding["profile_version"],
            binding["connection_id"],
            binding["account_hash"],
            "prepare-only-v1",
        ]
        routine_id = (
            "routine-"
            + hashlib.sha256(
                json.dumps(identity, separators=(",", ":")).encode()
            ).hexdigest()[:32]
        )
        key = _key(user_id, f"ROUTINE#{routine_id}")
        existing = self._get(key)
        if existing:
            if _string(existing, "user_id") != user_id:
                raise RoutineConflict("Routine owner changed")
            return existing
        if len(self.list(user_id)) >= MAX_ROUTINES:
            raise RoutineLimitError("Routine limit is 32")
        label = (
            "deadline" if binding["opportunity_type"] == "DEADLINE" else "appointment"
        )
        fields = {
            **binding,
            "entity_type": "mail_routine",
            "user_id": user_id,
            "routine_id": routine_id,
            "status": "PROPOSED",
            "mode": "PREPARE_ONLY",
            "version": 1,
            "title": f"Prepare new {label} mail",
            "description": f"Prepare tasks from new {label} mail from this sender. External actions need separate approval.",
            "created_at": now,
            "updated_at": now,
        }
        item = {
            **key,
            **{
                field: {"N": str(value)} if type(value) is int else {"S": value}
                for field, value in fields.items()
            },
            "activated_at": {"NULL": True},
        }
        try:
            self._transact(
                [
                    *guards,
                    {
                        "Update": {
                            "TableName": self.table,
                            "Key": _key(user_id, "ROUTINE_LIMITS"),
                            "UpdateExpression": "SET user_id=if_not_exists(user_id,:owner),routine_count=if_not_exists(routine_count,:zero)+:one",
                            "ConditionExpression": "attribute_not_exists(PK) OR (user_id=:owner AND routine_count<:limit)",
                            "ExpressionAttributeValues": {
                                ":owner": {"S": user_id},
                                ":zero": {"N": "0"},
                                ":one": {"N": "1"},
                                ":limit": {"N": str(MAX_ROUTINES)},
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table,
                            "Item": item,
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                ]
            )
        except RoutineConflict:
            existing = self._get(key)
            if existing and _string(existing, "user_id") == user_id:
                return existing
            if (
                _integer(self._get(_key(user_id, "ROUTINE_LIMITS")), "routine_count")
                or 0
            ) >= MAX_ROUTINES:
                raise RoutineLimitError("Routine limit is 32") from None
            raise
        return item

    def set_status(
        self, user_id: str, routine_id: str, version: int, status: str, now: str
    ):
        item = self._get(_key(user_id, f"ROUTINE#{routine_id}"))
        if not item or _string(item, "user_id") != user_id:
            raise RoutineNotFound("Routine not found")
        if _integer(item, "version") != version:
            raise RoutineConflict("Routine version changed")
        if _string(item, "mode") != "PREPARE_ONLY" or _string(item, "status") not in {
            "PROPOSED",
            "ACTIVE",
            "PAUSED",
        }:
            raise RoutineNotReady("Routine is unavailable")
        guards = []
        if status == "ACTIVE":
            binding, guards = self._source(
                user_id,
                _string(item, "source_case_id"),
                _integer(item, "source_case_version"),
            )
            if any(
                item.get(field)
                != ({"N": str(value)} if type(value) is int else {"S": value})
                for field, value in binding.items()
            ):
                raise RoutineNotReady("Routine source, connection or profile changed")
        if _string(item, "status") == status:
            return item
        next_item = {
            **item,
            "status": {"S": status},
            "version": {"N": str(version + 1)},
            "updated_at": {"S": now},
        }
        if status == "ACTIVE":
            next_item.update(
                activated_at={"S": now},
                GSI1PK={"S": f"USER#{user_id}#ROUTINE#ACTIVE"},
                GSI1SK={"S": f"{now}#{routine_id}"},
            )
        else:
            next_item.pop("GSI1PK", None)
            next_item.pop("GSI1SK", None)
        condition = _snapshot(
            self.table, item, ("user_id", "version", "status", "mode")
        )["ConditionCheck"]
        condition.pop("Key")
        self._transact([*guards, {"Put": {**condition, "Item": next_item}}])
        return next_item

    def public(self, item: Mapping[str, Any]) -> dict[str, Any]:
        user_id, status = _string(item, "user_id"), _string(item, "status")
        effective, reason = status, None
        if status != "PAUSED":
            try:
                connection, _mail, profile = self._current(user_id)
                if _string(connection, "account_hash") != _string(
                    item, "account_hash"
                ) or _string(connection, "mail_connection_id") != _string(
                    item, "connection_id"
                ):
                    reason = REVIEW_REASONS["connection"]
                elif profile["version"] != _integer(item, "profile_version"):
                    reason = REVIEW_REASONS["profile"]
                else:
                    meta = self._get(
                        {
                            "PK": {"S": f"CASE#{_string(item, 'source_case_id')}"},
                            "SK": {"S": "META"},
                        }
                    )
                    if (
                        _string(meta, "user_id") != user_id
                        or _string(meta, "status") != "COMPLETED"
                        or _integer(meta, "version")
                        != _integer(item, "source_case_version")
                    ):
                        reason = REVIEW_REASONS["source"]
            except RoutineNotReady as error:
                reason = (
                    str(error)
                    if str(error) in REVIEW_REASONS.values()
                    else REVIEW_REASONS["source"]
                )
            if reason:
                effective = "REVIEW_REQUIRED"
        return {
            **{
                field: _string(item, field)
                for field in (
                    "routine_id",
                    "status",
                    "source_case_id",
                    "title",
                    "description",
                    "sender_domain",
                    "opportunity_type",
                    "mode",
                    "activated_at",
                    "created_at",
                    "updated_at",
                )
            },
            "version": _integer(item, "version"),
            "effective_status": effective,
            "review_reason": reason,
        }


def default_routine_service() -> RoutineService:
    import os

    import boto3

    table = os.environ.get("MAIN_TABLE_NAME", "").strip()
    if not table:
        raise RuntimeError("MAIN_TABLE_NAME is required")
    return RoutineService(DynamoRoutineStore(table, boto3.client("dynamodb")))
