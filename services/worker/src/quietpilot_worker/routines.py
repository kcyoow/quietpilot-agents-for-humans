"""Bounded PREPARE_ONLY mail routines with atomic Candidate consume and dispatch recovery."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .google_connection_store import (
    _candidate_key,
    _connection_key,
    _dynamo_text,
    _evidence_key,
    _snapshot_guard,
)

MAX_ROUTINES = 32
MAX_CANDIDATES_PER_GROUP = 50
MAX_PENDING_DISPATCHES = 32
GROUPS = ("appointments", "deadlines", "follow-ups")
LOCAL_CAPABILITIES = {
    "prepare_task": "quietpilot.task.prepare",
    "prepare_reminder": "quietpilot.reminder.prepare",
}
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
_DOMAIN = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


class RoutineDispatchError(RuntimeError):
    pass


def _key(user_id: str, suffix: str):
    return {"PK": {"S": f"USER#{user_id}"}, "SK": {"S": suffix}}


def _number(item: Mapping[str, Any], field: str) -> int | None:
    raw = item.get(field, {}).get("N")
    return (
        int(raw) if isinstance(raw, str) and re.fullmatch(r"[0-9]{1,16}", raw) else None
    )


def _texts(item: Mapping[str, Any], field: str, maximum=12):
    raw = item.get(field, {}).get("L")
    if (
        not isinstance(raw, list)
        or len(raw) > maximum
        or any(
            not isinstance(value, dict)
            or not isinstance(value.get("S"), str)
            or len(value["S"]) > 500
            for value in raw
        )
    ):
        return None
    return [value["S"] for value in raw]


def _json(item, field):
    try:
        return json.loads(_dynamo_text(item, field) or "null")
    except (ValueError, TypeError):
        return None


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Routine clock requires a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sender_domain(evidence):
    facts = _texts(evidence, "facts")
    if facts is None:
        return None
    domains = [
        fact.removeprefix("sender_domain=").lower()
        for fact in facts
        if fact.startswith("sender_domain=")
    ]
    if len(domains) != 1 or _DOMAIN.fullmatch(domains[0]) is None:
        return None
    return domains[0]


def _domain_and_received(evidence):
    domain = _sender_domain(evidence)
    if domain is None:
        return None
    received = [
        fact.removeprefix("received_at_unix_ms=")
        for fact in _texts(evidence, "facts")
        if fact.startswith("received_at_unix_ms=")
    ]
    if len(received) != 1 or re.fullmatch(r"[0-9]{1,15}", received[0]) is None:
        return None
    return domain, int(received[0])


class SqsRoutineCaseQueue:
    def __init__(self, queue_url: str, client: Any):
        self.url, self.client = queue_url, client

    def send(self, *, user_id: str, case_id: str, connector: str, plan_version: int):
        envelope = {
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "event_type": "DIRECT_REQUEST_RECEIVED",
            "user_id": user_id,
            "connector": connector,
            "occurred_at": _timestamp(datetime.now(UTC)),
            "dedupe_key": f"case-plan:{case_id}:{plan_version}",
            "trace_id": str(uuid.uuid4()),
            "payload": {"case_id": case_id, "plan_version": plan_version},
        }
        self.client.send_message(
            QueueUrl=self.url,
            MessageBody=json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
        )


class DynamoRoutineStore:
    def __init__(self, table_name: str, client: Any):
        self.table, self.client = table_name, client

    def get(self, key):
        return (
            self.client.get_item(
                TableName=self.table, Key=key, ConsistentRead=True
            ).get("Item")
            or {}
        )

    def prefix(self, user_id, prefix, limit):
        result = self.client.query(
            TableName=self.table,
            KeyConditionExpression="PK=:pk AND begins_with(SK,:prefix)",
            ExpressionAttributeValues={
                ":pk": {"S": f"USER#{user_id}"},
                ":prefix": {"S": prefix},
            },
            ConsistentRead=True,
            Limit=limit + 1,
        )
        rows = result.get("Items", [])
        return rows[:limit], len(rows) > limit or bool(result.get("LastEvaluatedKey"))

    def candidates(self, user_id, group):
        result = self.client.query(
            TableName=self.table,
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK=:pk",
            ExpressionAttributeValues={
                ":pk": {"S": f"USER#{user_id}#CANDIDATE#VISIBLE#{group}"}
            },
            ScanIndexForward=False,
            Limit=MAX_CANDIDATES_PER_GROUP + 1,
        )
        rows = result.get("Items", [])
        return rows[:MAX_CANDIDATES_PER_GROUP], len(
            rows
        ) > MAX_CANDIDATES_PER_GROUP or bool(result.get("LastEvaluatedKey"))

    def guard(self, item, fields=None, *, key=None):
        return {
            "ConditionCheck": {
                "TableName": self.table,
                "Key": key or {field: item[field] for field in ("PK", "SK")},
                **_snapshot_guard(
                    item,
                    fields
                    or tuple(field for field in item if field not in {"PK", "SK"}),
                ),
            }
        }

    def current(self, user_id):
        connection = self.get(_connection_key(user_id))
        mail = self.get(_key(user_id, "MAIL_INTERESTS#google"))
        profile, scan = _json(mail, "profile_json"), _json(mail, "scan_json")
        if (
            _dynamo_text(connection, "status") != "CONNECTED"
            or not _dynamo_text(connection, "mail_connection_id")
            or re.fullmatch(
                r"[0-9a-f]{64}", _dynamo_text(connection, "account_hash") or ""
            )
            is None
            or GMAIL_SCOPE not in (_texts(connection, "granted_scopes", 20) or [])
            or not isinstance(profile, dict)
            or not (profile.get("tags") or str(profile.get("description", "")).strip())
            or type(profile.get("version")) is not int
            or profile["version"] < 1
            or _number(mail, "profile_version") != profile["version"]
            or not isinstance(scan, dict)
            or scan.get("profile_version") != profile["version"]
            or not scan.get("scan_id")
            or _dynamo_text(mail, "scan_connection_id")
            != _dynamo_text(connection, "mail_connection_id")
        ):
            return None
        return connection, mail, profile, scan

    def rule_guards(self, user_id, rule, current):
        connection, _mail, profile, _scan = current
        if (
            _dynamo_text(rule, "user_id") != user_id
            or _dynamo_text(rule, "status") != "ACTIVE"
            or _dynamo_text(rule, "mode") != "PREPARE_ONLY"
            or not _number(rule, "version")
            or _dynamo_text(rule, "connection_id")
            != _dynamo_text(connection, "mail_connection_id")
            or _dynamo_text(rule, "account_hash")
            != _dynamo_text(connection, "account_hash")
            or _number(rule, "profile_version") != profile["version"]
        ):
            return None
        case_id = _dynamo_text(rule, "source_case_id")
        meta = self.get({"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": "META"}})
        rows = {}
        for field in (
            "source_execution_sk",
            "source_approval_sk",
            "source_evidence_sk",
        ):
            suffix = _dynamo_text(rule, field)
            if not suffix:
                return None
            rows[field] = self.get(
                {"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": suffix}}
            )
        execution, approval, source = (
            rows[field]
            for field in (
                "source_execution_sk",
                "source_approval_sk",
                "source_evidence_sk",
            )
        )
        source_domain = _sender_domain(source)
        if (
            _dynamo_text(meta, "user_id") != user_id
            or _dynamo_text(meta, "status") != "COMPLETED"
            or _number(meta, "version") != _number(rule, "source_case_version")
            or _number(meta, "current_plan_version")
            != _number(rule, "source_plan_version")
            or _dynamo_text(meta, "current_plan_hash")
            != _dynamo_text(rule, "source_plan_hash")
            or _dynamo_text(meta, "google_account_hash")
            != _dynamo_text(rule, "account_hash")
            or _dynamo_text(meta, "mail_connection_id")
            != _dynamo_text(rule, "source_connection_id")
            or _dynamo_text(execution, "user_id") != user_id
            or _dynamo_text(execution, "status") != "SUCCEEDED"
            or execution.get("verified") != {"BOOL": True}
            or _dynamo_text(execution, "operation_id")
            != _dynamo_text(meta, "approved_operation_id")
            or _dynamo_text(execution, "plan_hash")
            != _dynamo_text(rule, "source_plan_hash")
            or _number(execution, "plan_version")
            != _number(rule, "source_plan_version")
            or _dynamo_text(approval, "user_id") != user_id
            or _dynamo_text(approval, "decision") != "APPROVE"
            or _dynamo_text(approval, "grant_mode") != "ONCE"
            or _dynamo_text(approval, "account_hash")
            != _dynamo_text(rule, "account_hash")
            or _dynamo_text(approval, "mail_connection_id")
            != _dynamo_text(rule, "source_connection_id")
            or _dynamo_text(source, "user_id") != user_id
            or _dynamo_text(source, "source") != "gmail"
            or _dynamo_text(source, "evidence_ref")
            != _dynamo_text(rule, "source_evidence_ref")
            or _number(source, "revision") != _number(rule, "source_evidence_revision")
            or source_domain is None
            or source_domain != _dynamo_text(rule, "sender_domain")
        ):
            return None
        return [self.guard(row) for row in (rule, meta, execution, approval, source)]

    def create_case(
        self,
        user_id,
        candidate,
        source,
        action,
        rule,
        guards,
        current,
        suppression,
        suppression_key,
        now,
    ):
        connection, mail, _profile, _scan = current
        candidate_id, reference = (
            _dynamo_text(candidate, "candidate_id"),
            _dynamo_text(source, "evidence_ref"),
        )
        case_id = (
            "routine-"
            + hashlib.sha256(f"{user_id}:{candidate_id}".encode()).hexdigest()[:32]
        )
        pk, stamp = {"S": f"CASE#{case_id}"}, _timestamp(now)
        routine_id, routine_version = (
            _dynamo_text(rule, "routine_id"),
            _number(rule, "version"),
        )
        capability = LOCAL_CAPABILITIES[action["verb"]]
        meta = {
            "PK": pk,
            "SK": {"S": "META"},
            "entity_type": {"S": "case"},
            "case_id": {"S": case_id},
            "user_id": {"S": user_id},
            "case_type": {"S": "ROUTINE_DISCOVERY"},
            "goal": candidate["outcome"],
            "summary": candidate["summary"],
            "status": {"S": "PREPARING"},
            "risk": {"S": action["risk"]},
            "priority": {"N": "50"},
            "providers": {"L": [{"S": "google"}]},
            "why_now": {"S": "New mail matches an active routine."},
            "next_action": {"S": "Preparing a task from the new email."},
            "evidence_refs": {"L": [{"S": reference}]},
            "requested_plan_version": {"N": "1"},
            "version": {"N": "1"},
            "created_at": {"S": stamp},
            "updated_at": {"S": stamp},
            "GSI1PK": {"S": f"USER#{user_id}#CASE#ACTIVE"},
            "GSI1SK": {"S": f"999949#{stamp}#{case_id}"},
            "required_capabilities": {"L": [{"S": capability}]},
            "requested_actions_json": {
                "S": json.dumps(
                    [action], ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
            },
            "google_account_hash": connection["account_hash"],
            "mail_connection_id": connection["mail_connection_id"],
            "origin_routine_id": {"S": routine_id},
            "origin_routine_version": {"N": str(routine_version)},
            "routine_mode": {"S": "PREPARE_ONLY"},
            "dispatch_pending": {"BOOL": True},
        }
        evidence_id = hashlib.sha256(reference.encode()).hexdigest()[:24]
        evidence = {
            "PK": pk,
            "SK": {"S": f"EVIDENCE#{evidence_id}"},
            "entity_type": {"S": "case_evidence"},
            "user_id": {"S": user_id},
            "evidence_id": {"S": evidence_id},
            "evidence_ref": {"S": reference},
            "provider": {"S": "google"},
            "label": source["title"],
            "detail": {"S": "Gmail source"},
            "revision": source["revision"],
            "source": {"S": "gmail"},
            "title": source["title"],
            "facts": source["facts"],
            "created_at": {"S": stamp},
        }
        timeline = {
            "PK": pk,
            "SK": {"S": f"EVENT#{stamp}#routine"},
            "entity_type": {"S": "audit_event"},
            "event_id": {"S": f"routine-{candidate_id}"},
            "label": {"S": "Prepared by mail routine"},
            "body": {
                "S": "An active routine prepares this task. External actions need separate approval."
            },
            "state": {"S": "DONE"},
            "occurred_at": {"S": stamp},
        }
        pending = {
            **_key(user_id, f"ROUTINE_DISPATCH#{case_id}"),
            "entity_type": {"S": "routine_dispatch"},
            "user_id": {"S": user_id},
            "case_id": {"S": case_id},
            "routine_id": {"S": routine_id},
            "plan_version": {"N": "1"},
        }
        candidate_guard = _snapshot_guard(
            candidate, tuple(field for field in candidate if field not in {"PK", "SK"})
        )
        candidate_guard["ExpressionAttributeNames"].update(
            {"#status": "status", "#version": "version"}
        )
        candidate_guard["ExpressionAttributeValues"].update(
            {
                ":converted": {"S": "CONVERTED"},
                ":one": {"N": "1"},
                ":now": {"S": stamp},
                ":case": {"S": case_id},
            }
        )
        transaction = [
            *guards,
            self.guard(
                connection,
                ("status", "account_hash", "mail_connection_id", "granted_scopes"),
            ),
            self.guard(
                mail,
                (
                    "profile_version",
                    "profile_json",
                    "scan_id",
                    "scan_json",
                    "scan_connection_id",
                ),
            ),
            self.guard(source),
            self.guard(
                suppression,
                ("user_id", "group_id", "active", "version"),
                key=suppression_key,
            ),
            *[
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": item,
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                }
                for item in (meta, evidence, timeline, pending)
            ],
            {
                "Update": {
                    "TableName": self.table,
                    "Key": _candidate_key(user_id, candidate_id),
                    **candidate_guard,
                    "UpdateExpression": "SET #status=:converted,#version=#version+:one,updated_at=:now,converted_case_id=:case REMOVE GSI1PK,GSI1SK",
                }
            },
        ]
        try:
            self.client.transact_write_items(TransactItems=transaction)
        except self.client.exceptions.TransactionCanceledException:
            return None
        return pending

    def acknowledge(self, user_id, pending, meta):
        marker_guard = _snapshot_guard(
            pending, ("user_id", "case_id", "routine_id", "plan_version")
        )
        update = {
            "TableName": self.table,
            "Key": {field: meta[field] for field in ("PK", "SK")},
            "UpdateExpression": "SET dispatch_pending=:false",
            "ConditionExpression": "user_id=:user AND origin_routine_id=:routine",
            "ExpressionAttributeValues": {
                ":false": {"BOOL": False},
                ":user": {"S": user_id},
                ":routine": pending["routine_id"],
            },
        }
        self.client.transact_write_items(
            TransactItems=[
                {"Update": update},
                {
                    "Delete": {
                        "TableName": self.table,
                        "Key": {field: pending[field] for field in ("PK", "SK")},
                        **marker_guard,
                    }
                },
            ]
        )


class RoutineProcessor:
    def __init__(
        self,
        store: DynamoRoutineStore,
        queue: Any,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.store, self.queue, self.clock = store, queue, clock

    def _dispatch(self, user_id, pending):
        if (
            _dynamo_text(pending, "user_id") != user_id
            or _number(pending, "plan_version") != 1
        ):
            return False
        case_id = _dynamo_text(pending, "case_id")
        meta = self.store.get({"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": "META"}})
        if (
            not meta
            or _dynamo_text(meta, "user_id") != user_id
            or _dynamo_text(meta, "origin_routine_id")
            != _dynamo_text(pending, "routine_id")
        ):
            return False
        if (
            _dynamo_text(meta, "status") == "PREPARING"
            and _number(meta, "requested_plan_version") == 1
            and not _number(meta, "current_plan_version")
            and meta.get("dispatch_pending") == {"BOOL": True}
        ):
            try:
                self.queue.send(
                    user_id=user_id, case_id=case_id, connector="google", plan_version=1
                )
            except Exception:  # noqa: BLE001 - keep provider payloads out of retry errors
                raise RoutineDispatchError("Routine Case dispatch failed") from None
            sent = True
        else:
            sent = False
        try:
            self.store.acknowledge(user_id, pending, meta)
        except Exception:  # noqa: BLE001 - retain the durable marker for a safe retry
            raise RoutineDispatchError(
                "Routine Case dispatch acknowledgement failed"
            ) from None
        return sent

    def process(self, user_id: str) -> dict[str, Any]:
        if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 128:
            raise ValueError("Routine owner is invalid")
        now = self.clock()
        _timestamp(now)
        stats = {
            "rules_checked": 0,
            "candidates_checked": 0,
            "created": 0,
            "dispatched": 0,
            "recovered": 0,
            "skipped": 0,
            "conflicts": 0,
            "limit_reached": False,
        }
        pending, limited = self.store.prefix(
            user_id, "ROUTINE_DISPATCH#", MAX_PENDING_DISPATCHES
        )
        stats["limit_reached"] |= limited
        for marker in pending:
            if self._dispatch(user_id, marker):
                stats["dispatched"] += 1
                stats["recovered"] += 1
        if limited:
            # Finish durable recovery on the next bounded invocation before
            # creating more Cases that would compete with the existing backlog.
            return stats
        current = self.store.current(user_id)
        if current is None:
            return stats
        rules, limited = self.store.prefix(user_id, "ROUTINE#", MAX_ROUTINES)
        stats["limit_reached"] |= limited
        if limited:
            return stats
        active = []
        for rule in sorted(
            rules, key=lambda item: _dynamo_text(item, "routine_id") or ""
        ):
            if _dynamo_text(rule, "status") != "ACTIVE":
                continue
            stats["rules_checked"] += 1
            guards = self.store.rule_guards(user_id, rule, current)
            if guards is not None:
                try:
                    activated = datetime.fromisoformat(
                        _dynamo_text(rule, "activated_at") or ""
                    )
                    if activated.tzinfo is None:
                        raise ValueError
                    active.append((rule, guards, int(activated.timestamp() * 1000)))
                except (ValueError, OverflowError):
                    pass
        if not active:
            return stats
        connection, _mail, profile, scan = current
        applicable_groups = {
            {"DEADLINE": "deadlines", "APPOINTMENT": "appointments"}.get(
                _dynamo_text(rule, "opportunity_type")
            )
            for rule, _guards, _activated in active
        }
        for group in GROUPS:
            if group not in applicable_groups:
                continue
            suppression_id = hashlib.sha256(f"{user_id}:{group}".encode()).hexdigest()[
                :24
            ]
            suppression_key = _key(user_id, f"SUPPRESSION#{suppression_id}")
            suppression = self.store.get(suppression_key)
            if suppression.get("active") == {"BOOL": True}:
                continue
            candidates, limited = self.store.candidates(user_id, group)
            stats["limit_reached"] |= limited
            for candidate in candidates:
                stats["candidates_checked"] += 1
                checked = self._candidate(
                    user_id, candidate, group, connection, profile, scan
                )
                if checked is None:
                    stats["skipped"] += 1
                    continue
                source, action, domain, received = checked
                matched = False
                for rule, guards, activated in active:
                    if (
                        domain != _dynamo_text(rule, "sender_domain")
                        or _dynamo_text(candidate, "opportunity_type")
                        != _dynamo_text(rule, "opportunity_type")
                        or not activated < received <= int(now.timestamp() * 1000)
                    ):
                        continue
                    matched = True
                    marker = self.store.create_case(
                        user_id,
                        candidate,
                        source,
                        action,
                        rule,
                        guards,
                        current,
                        suppression,
                        suppression_key,
                        now,
                    )
                    if marker is None:
                        stats["conflicts"] += 1
                        continue
                    stats["created"] += 1
                    stats["dispatched"] += int(self._dispatch(user_id, marker))
                    break
                if not matched:
                    stats["skipped"] += 1
        return stats

    def _candidate(self, user_id, candidate, group, connection, profile, scan):
        candidate_id = _dynamo_text(candidate, "candidate_id")
        refs = _texts(candidate, "evidence_refs", 8)
        kind = _dynamo_text(candidate, "opportunity_type")
        actions = _json(candidate, "proposed_actions_json")
        try:
            confidence = Decimal(candidate.get("confidence", {}).get("N", "NaN"))
        except InvalidOperation:
            return None
        if (
            not candidate_id
            or candidate.get("PK") != {"S": f"USER#{user_id}"}
            or candidate.get("SK") != {"S": f"CANDIDATE#{candidate_id}"}
            or _dynamo_text(candidate, "user_id") != user_id
            or _dynamo_text(candidate, "status") != "VISIBLE"
            or not _number(candidate, "version")
            or _dynamo_text(candidate, "source_type") != "CONNECTED_SIGNAL"
            or not confidence.is_finite()
            or not Decimal("0.7") <= confidence <= 1
            or _number(candidate, "mail_profile_version") != profile["version"]
            or _dynamo_text(candidate, "mail_scan_id") != scan["scan_id"]
            or _dynamo_text(candidate, "mail_connection_id")
            != _dynamo_text(connection, "mail_connection_id")
            or kind not in {"DEADLINE", "APPOINTMENT"}
            or group
            != {"DEADLINE": "deadlines", "APPOINTMENT": "appointments"}.get(kind)
            or _dynamo_text(candidate, "primary_group_id") != group
            or not refs
            or len(refs) != 1
            or not isinstance(actions, list)
            or len(actions) != 1
            or not isinstance(actions[0], dict)
        ):
            return None
        action = actions[0]
        parameters = action.get("parameters")
        verb = action.get("verb")
        ranks = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        if (
            set(action)
            != {
                "connector",
                "target_resource",
                "verb",
                "parameters",
                "required_scopes",
                "risk",
                "reversible",
                "verification_method",
            }
            or action.get("connector") != "quietpilot"
            or verb not in LOCAL_CAPABILITIES
            or action.get("required_scopes") != []
            or action.get("reversible") is not True
            or action.get("risk") not in ranks
            or _dynamo_text(candidate, "risk") not in ranks
            or ranks[action["risk"]] < ranks[_dynamo_text(candidate, "risk")]
            or _texts(candidate, "required_capabilities", 8)
            != [LOCAL_CAPABILITIES[verb]]
            or not isinstance(parameters, dict)
            or set(parameters) - {"source_ref", "title"}
            or parameters.get("source_ref") != refs[0]
            or any(
                not isinstance(value, str) or not value.strip() or len(value) > 500
                for value in parameters.values()
            )
            or any(
                not isinstance(action.get(field), str)
                or not action[field].strip()
                or len(action[field]) > 500
                for field in ("target_resource", "verification_method")
            )
            or not 0 < len(_dynamo_text(candidate, "outcome") or "") <= 300
            or not 0 < len(_dynamo_text(candidate, "summary") or "") <= 2000
        ):
            return None
        source = self.store.get(_evidence_key(user_id, refs[0]))
        if (
            _dynamo_text(source, "user_id") != user_id
            or _dynamo_text(source, "source") != "gmail"
            or _dynamo_text(source, "evidence_ref") != refs[0]
            or re.fullmatch(r"gmail:[0-9a-f]{64}", refs[0]) is None
            or not _number(source, "revision")
            or not 0 < len(_dynamo_text(source, "title") or "") <= 200
        ):
            return None
        facts = _domain_and_received(source)
        if facts is None:
            return None
        return source, action, *facts


def default_routine_processor() -> RoutineProcessor:
    import os

    import boto3

    table, url = (
        os.environ.get("MAIN_TABLE_NAME", "").strip(),
        os.environ.get("WORK_QUEUE_URL", "").strip(),
    )
    if not table or not url:
        raise RuntimeError("Routine worker configuration is incomplete")
    return RoutineProcessor(
        DynamoRoutineStore(table, boto3.client("dynamodb")),
        SqsRoutineCaseQueue(url, boto3.client("sqs")),
    )
