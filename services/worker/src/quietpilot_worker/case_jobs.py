"""Proposal-only Case preparation jobs for the live mobile workspace."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .google_jobs import BotoAgentRuntime

CASE_TYPES = frozenset(
    {
        "CONNECTED_SIGNAL",
        "DIRECT_DELEGATION",
        "ROUTINE_DISCOVERY",
        "EXCEPTION_APPROVAL",
    }
)
RISKS = frozenset({"LOW", "MEDIUM", "HIGH"})
MAX_EVIDENCE = 8
MAX_ACTIONS = 8
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
LOCAL_ARTIFACTS = {
    "prepare_reply": ("REPLY_DRAFT", "quietpilot.reply.prepare"),
    "prepare_task": ("CHECKLIST", "quietpilot.task.prepare"),
    "prepare_reminder": ("REMINDER", "quietpilot.reminder.prepare"),
}
logger = logging.getLogger(__name__)


class ProposalRuntime(Protocol):
    def invoke_payload(
        self, user_id: str, payload: Mapping[str, object]
    ) -> dict[str, object]: ...


class CasePreparationStore(Protocol):
    def load(
        self, user_id: str, case_id: str, plan_version: int
    ) -> dict[str, object] | None: ...

    def write_plan(
        self,
        user_id: str,
        case_id: str,
        plan_version: int,
        *,
        plan: dict[str, object],
        summary: str,
        decision_question: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class CaseJobProcessor:
    runtime: ProposalRuntime
    store: CasePreparationStore

    def process(self, envelope: Mapping[str, object]) -> None:
        if envelope.get("event_type") != "DIRECT_REQUEST_RECEIVED":
            raise ValueError("Case job event type is invalid")
        user_id = envelope.get("user_id")
        if not isinstance(user_id, str) or not user_id or len(user_id) > 256:
            raise ValueError("Case job user_id is invalid")
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {
            "case_id",
            "plan_version",
        }:
            raise ValueError("Case job payload is invalid")
        case_id = payload.get("case_id")
        plan_version = payload.get("plan_version")
        if not isinstance(case_id, str) or not case_id or len(case_id) > 128:
            raise ValueError("Case job case_id is invalid")
        if type(plan_version) is not int or plan_version < 1:
            raise ValueError("Case job plan_version is invalid")

        context = self.store.load(user_id, case_id, plan_version)
        if context is None:
            return
        if context.get("preparation_error") == "CASE_PREPARATION_FAILED":
            result = {"error_code": "CASE_PREPARATION_FAILED"}
        else:
            invocation = _agent_invocation(user_id, context)
            try:
                result = self.runtime.invoke_payload(user_id, invocation)
            except Exception as error:  # noqa: BLE001 - preparation has no external writes
                logger.warning(
                    "case_preparation_failed stage=runtime error_class=%s",
                    type(error).__name__,
                )
                result = {"error_code": "CASE_PREPARATION_FAILED"}
        plan, summary, decision_question = _plan_from_result(
            {**context, "user_id": user_id}, result
        )
        self.store.write_plan(
            user_id,
            case_id,
            plan_version,
            plan=plan,
            summary=summary,
            decision_question=decision_question,
        )


class DynamoCasePreparationStore:
    def __init__(self, table_name: str, client: Any) -> None:
        self._table_name = table_name
        self._client = client

    @classmethod
    def from_environment(cls) -> DynamoCasePreparationStore:
        import boto3

        return cls(_required_environment("MAIN_TABLE_NAME"), boto3.client("dynamodb"))

    def load(
        self, user_id: str, case_id: str, plan_version: int
    ) -> dict[str, object] | None:
        response = self._client.query(
            TableName=self._table_name,
            KeyConditionExpression="#pk=:pk",
            ExpressionAttributeNames={"#pk": "PK"},
            ExpressionAttributeValues={":pk": {"S": f"CASE#{case_id}"}},
            ConsistentRead=True,
            Limit=101,
        )
        items = response.get("Items")
        if not isinstance(items, list) or not items:
            return None
        if len(items) > 100 or response.get("LastEvaluatedKey"):
            raise RuntimeError("Case preparation context exceeds its bound")
        meta = next(
            (
                item
                for item in items
                if isinstance(item, Mapping) and _string(item, "SK") == "META"
            ),
            None,
        )
        if not isinstance(meta, Mapping) or _string(meta, "user_id") != user_id:
            return None
        if (
            _string(meta, "status") != "PREPARING"
            or _integer(meta, "requested_plan_version") != plan_version
            or (_integer(meta, "current_plan_version") or 0) >= plan_version
        ):
            return None
        case_type = _string(meta, "case_type")
        goal = _string(meta, "goal")
        risk = _string(meta, "risk")
        evidence_refs = _string_list(meta, "evidence_refs")
        if (
            case_type not in CASE_TYPES
            or not goal
            or risk not in RISKS
            or not 1 <= len(evidence_refs) <= MAX_EVIDENCE
        ):
            raise TypeError("Case preparation metadata is invalid")
        evidence_by_ref: dict[str, dict[str, object]] = {}
        for item in items:
            if (
                not isinstance(item, Mapping)
                or _string(item, "entity_type") != "case_evidence"
            ):
                continue
            reference = _string(item, "evidence_ref")
            if reference is None:
                raise TypeError("Case evidence reference is invalid")
            record: dict[str, object] = {
                "user_id": user_id,
                "ref": reference,
                "revision": _integer(item, "revision") or 1,
                "source": _required_string(item, "source"),
                "title": _required_string(item, "title"),
                "facts": _string_list(item, "facts"),
                "untrusted_text": _string(item, "untrusted_text"),
            }
            evidence_by_ref[reference] = record
        if set(evidence_by_ref) != set(evidence_refs):
            raise TypeError("Case evidence set is incomplete")
        capability_ids = _string_list(meta, "required_capabilities")
        requested_actions = _json_actions(meta, "requested_actions_json")
        if requested_actions and not capability_ids:
            raise TypeError("Case actions have no capability envelope")
        calendar_account = self._calendar_account(user_id, meta)
        gmail_account = (
            self._gmail_account(user_id, meta)
            if any(record["source"] == "gmail" for record in evidence_by_ref.values())
            else None
        )
        if calendar_account:
            capability_ids = list(
                dict.fromkeys([*capability_ids, "google.calendar.events.create"])
            )
        previous_context: dict[str, object] = {}
        try:
            previous = _previous_local_preparation(
                user_id, case_id, plan_version, meta, items, requested_actions
            )
            if previous is not None:
                previous_context["previous_local_preparation"] = previous
        except (TypeError, ValueError, KeyError):
            previous_context["preparation_error"] = "CASE_PREPARATION_FAILED"
        return {
            "case_id": case_id,
            "case_type": case_type,
            "goal": goal,
            "plan_version": plan_version,
            "risk": risk,
            "evidence": [evidence_by_ref[ref] for ref in evidence_refs],
            "capability_ids": capability_ids,
            "requested_actions": requested_actions,
            **previous_context,
            **(
                {"google_account_hash": gmail_account or calendar_account}
                if gmail_account or calendar_account
                else {}
            ),
        }

    def _gmail_account(self, user_id: str, meta: Mapping[str, Any]) -> str | None:
        item = self._client.get_item(
            TableName=self._table_name,
            Key={"PK": {"S": f"USER#{user_id}"}, "SK": {"S": "CONNECTION#google"}},
            ConsistentRead=True,
        ).get("Item", {})
        account = _string(item, "account_hash")
        if (
            _string(item, "status") != "CONNECTED"
            or "https://www.googleapis.com/auth/gmail.readonly"
            not in _string_list(item, "granted_scopes")
            or not account
            or _string(meta, "google_account_hash") != account
        ):
            return None
        return account

    def _calendar_account(self, user_id: str, meta: Mapping[str, Any]) -> str | None:
        def read(suffix: str) -> Mapping[str, Any]:
            return self._client.get_item(
                TableName=self._table_name,
                Key={"PK": {"S": f"USER#{user_id}"}, "SK": {"S": suffix}},
                ConsistentRead=True,
            ).get("Item", {})

        calendar = read("CONNECTION#google-calendar")
        if _string(
            calendar, "status"
        ) != "CONNECTED" or CALENDAR_SCOPE not in _string_list(
            calendar, "granted_scopes"
        ):
            return None
        mail = read("CONNECTION#google")
        epoch, account = (
            _string(mail, "mail_connection_id"),
            _string(mail, "account_hash"),
        )
        if (
            _string(mail, "status") != "CONNECTED"
            or not epoch
            or not account
            or (
                _string(calendar, "mail_connection_id"),
                _string(calendar, "account_hash"),
            )
            != (epoch, account)
        ):
            return None
        if (
            "google" in _string_list(meta, "providers")
            and _string(meta, "google_account_hash") != account
        ):
            return None
        return account

    def write_plan(
        self,
        user_id: str,
        case_id: str,
        plan_version: int,
        *,
        plan: dict[str, object],
        summary: str,
        decision_question: str,
    ) -> None:
        from .notifications import notification_put

        meta = self._client.get_item(
            TableName=self._table_name,
            Key={"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": "META"}},
            ConsistentRead=True,
        ).get("Item", {})
        if (
            _string(meta, "user_id") != user_id
            or _string(meta, "status") != "PREPARING"
            or _integer(meta, "requested_plan_version") != plan_version
            or (_integer(meta, "current_plan_version") or 0) >= plan_version
        ):
            return
        case_version = _integer(meta, "version")
        if case_version is None or case_version < 1:
            raise TypeError("Case preparation version is invalid")
        now = _now()
        local_status = plan.get("local_preparation_status")
        completed = local_status in {"READY", "NO_ACTION"}
        case_status = "COMPLETED" if completed else "DECISION_REQUIRED"
        notification = (
            []
            if completed
            else [
                notification_put(
                    self._table_name,
                    user_id,
                    case_id,
                    case_version + 1,
                    self._plan_notification_kind(meta, plan),
                    now,
                )
            ]
        )
        plan_hash = str(plan["hash"])
        plan_item = {
            "PK": {"S": f"CASE#{case_id}"},
            "SK": {"S": f"PLAN#{plan_version:06d}"},
            "entity_type": {"S": "plan"},
            "version": {"N": str(plan_version)},
            "plan_hash": {"S": plan_hash},
            "plan_json": {
                "S": json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
            },
            "created_at": {"S": now},
        }
        event_id = str(uuid.uuid4())
        event_item = {
            "PK": {"S": f"CASE#{case_id}"},
            "SK": {"S": f"EVENT#{now}#{event_id}"},
            "entity_type": {"S": "audit_event"},
            "event_id": {"S": event_id},
            "label": {
                "S": "Draft ready"
                if local_status == "READY"
                else "No action needed"
                if local_status == "NO_ACTION"
                else "Plan ready"
            },
            "body": {
                "S": decision_question
                if completed
                else "Prepared a plan using the source and available actions."
            },
            "state": {"S": "DONE" if completed else "CURRENT"},
            "occurred_at": {"S": now},
        }
        message_id = str(uuid.uuid4())
        message_item = {
            "PK": {"S": f"CASE#{case_id}"},
            "SK": {"S": f"MESSAGE#{now}#{message_id}"},
            "entity_type": {"S": "message"},
            "message_id": {"S": message_id},
            "author": {"S": "QUIETPILOT"},
            "text": {"S": decision_question},
            "created_at": {"S": now},
        }
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table_name,
                            "Key": {
                                "PK": {"S": f"CASE#{case_id}"},
                                "SK": {"S": "META"},
                            },
                            "UpdateExpression": (
                                "SET #status=:decision, summary=:summary, "
                                "risk=:risk, "
                                "current_plan_version=:plan_version, "
                                "current_plan_hash=:plan_hash, next_action=:next, "
                                "updated_at=:now, version=version+:one, GSI1SK=:gsi_sk"
                                + (", GSI1PK=:history" if completed else "")
                            ),
                            "ConditionExpression": (
                                "user_id=:user AND #status=:preparing AND "
                                "requested_plan_version=:plan_version AND "
                                "#meta_version=:expected_version"
                            ),
                            "ExpressionAttributeNames": {
                                "#status": "status",
                                "#meta_version": "version",
                            },
                            "ExpressionAttributeValues": {
                                ":decision": {"S": case_status},
                                ":summary": {"S": summary[:2000]},
                                ":risk": {"S": str(plan["risk"])},
                                ":plan_version": {"N": str(plan_version)},
                                ":plan_hash": {"S": plan_hash},
                                ":next": {"S": decision_question[:500]},
                                ":now": {"S": now},
                                ":one": {"N": "1"},
                                ":expected_version": {"N": str(case_version)},
                                ":user": {"S": user_id},
                                ":preparing": {"S": "PREPARING"},
                                ":gsi_sk": {"S": f"999949#{now}#{case_id}"},
                                **(
                                    {":history": {"S": f"USER#{user_id}#CASE#HISTORY"}}
                                    if completed
                                    else {}
                                ),
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": plan_item,
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    {"Put": {"TableName": self._table_name, "Item": event_item}},
                    {"Put": {"TableName": self._table_name, "Item": message_item}},
                    *notification,
                ]
            )
        except Exception as error:
            if _error_code(error) == "TransactionCanceledException":
                current = self.load(user_id, case_id, plan_version)
                if current is None:
                    return
            raise

    def _plan_notification_kind(
        self, meta: Mapping[str, Any], plan: Mapping[str, object]
    ) -> str:
        previous_version = _integer(meta, "current_plan_version")
        if previous_version:
            previous = self._client.get_item(
                TableName=self._table_name,
                Key={
                    "PK": meta["PK"],
                    "SK": {"S": f"PLAN#{previous_version:06d}"},
                },
                ConsistentRead=True,
            ).get("Item", {})
            try:
                old_plan = json.loads(_string(previous, "plan_json") or "null")
                if isinstance(old_plan, Mapping) and _notification_material(
                    old_plan
                ) != _notification_material(plan):
                    return "PLAN_CHANGED"
            except (TypeError, ValueError):
                pass
        return "DECISION_REQUIRED"


def _notification_material(plan: Mapping[str, object]) -> dict[str, object]:
    """Ignore version, generated action IDs and copy when comparing a prior plan."""
    fields = (
        "connector",
        "target",
        "verb",
        "parameters",
        "required_scopes",
        "risk",
        "reversible",
    )
    actions = plan.get("actions")
    if not isinstance(actions, list) or not all(
        isinstance(a, Mapping) for a in actions
    ):
        raise TypeError("Notification plan material is invalid")
    return {
        "actions": [
            {field: action.get(field) for field in fields} for action in actions
        ],
        "evidence_revisions": plan.get("evidence_revisions"),
        "risk": plan.get("risk"),
    }


def default_case_job_processor() -> CaseJobProcessor:
    return CaseJobProcessor(
        runtime=BotoAgentRuntime.from_environment(),
        store=DynamoCasePreparationStore.from_environment(),
    )


def _agent_invocation(user_id: str, context: Mapping[str, object]) -> dict[str, object]:
    evidence = context.get("evidence")
    if not isinstance(evidence, list):
        raise TypeError("Case evidence is invalid")
    capability_ids = context.get("capability_ids", [])
    requested_actions = context.get("requested_actions", [])
    if not isinstance(capability_ids, list) or not isinstance(requested_actions, list):
        raise TypeError("Case action envelope is invalid")
    capabilities = _capability_records(user_id, capability_ids)
    return {
        "request": {
            "user_id": user_id,
            "case_type": context["case_type"],
            "proposal_stage": "CASE_PLANNING",
            "goal": context["goal"],
            "evidence_refs": [str(item["ref"]) for item in evidence],
            "capability_ids": capability_ids,
            "primary_group_hint": "Direct request"
            if context["case_type"] == "DIRECT_DELEGATION"
            else "Mail and calendar",
            "tags": ["direct"]
            if context["case_type"] == "DIRECT_DELEGATION"
            else ["gmail"],
            "risk": context["risk"],
            "requested_actions": requested_actions,
            "conflicting_evidence": False,
        },
        "evidence": evidence,
        "capabilities": capabilities,
        **(
            {"google_account_hash": context["google_account_hash"]}
            if context.get("google_account_hash")
            else {}
        ),
        **(
            {"previous_local_preparation": context["previous_local_preparation"]}
            if context.get("previous_local_preparation") is not None
            else {}
        ),
    }


def _previous_local_preparation(
    user_id: str,
    case_id: str,
    requested_version: int,
    meta: Mapping[str, Any],
    items: list[object],
    requested_actions: list[dict[str, object]],
) -> dict[str, str] | None:
    """Read a validated former AI artifact as editing context, never as evidence."""
    version = _integer(meta, "current_plan_version")
    if version in (None, 0) and not _string(meta, "current_plan_hash"):
        return None
    if (
        len(requested_actions) != 1
        or requested_actions[0].get("connector") != "quietpilot"
        or requested_actions[0].get("verb") not in LOCAL_ARTIFACTS
    ):
        return None
    if not 1 <= version < requested_version:
        raise ValueError("Previous local preparation generation is invalid")
    plans = [
        item
        for item in items
        if isinstance(item, Mapping) and _string(item, "SK") == f"PLAN#{version:06d}"
    ]
    if len(plans) != 1:
        raise ValueError("Previous local preparation is unavailable")
    item = plans[0]
    plan = json.loads(_string(item, "plan_json") or "null")
    if not isinstance(plan, dict):
        raise TypeError("Previous local preparation is invalid")
    status = plan.get("local_preparation_status")
    if status not in {"READY", "NO_ACTION"}:
        return None
    if (
        _string(meta, "user_id") != user_id
        or "approved_operation_id" in meta
        or "approved_approval_id" in meta
        or any(
            isinstance(row, Mapping)
            and _string(row, "entity_type") in {"approval", "action_execution"}
            for row in items
        )
        or _string(item, "PK") != f"CASE#{case_id}"
        or _string(item, "entity_type") != "plan"
        or _integer(item, "version") != version
        or type(plan.get("version")) is not int
        or plan["version"] != version
        or plan.get("hash") != _string(meta, "current_plan_hash")
        or plan.get("hash") != _string(item, "plan_hash")
        or not isinstance(plan.get("hash"), str)
        or len(plan["hash"]) != 64
        or plan.get("risk") not in RISKS
        or plan.get("required_scopes") != []
        or plan.get("available_grant_modes") != []
    ):
        raise ValueError("Previous local preparation binding is invalid")
    revisions = plan.get("evidence_revisions")
    if not isinstance(revisions, dict) or not 1 <= len(revisions) <= MAX_EVIDENCE:
        raise ValueError("Previous local source revisions are invalid")
    current_refs = set(_string_list(meta, "evidence_refs"))
    for reference, revision in revisions.items():
        sources = [
            row
            for row in items
            if isinstance(row, Mapping)
            and _string(row, "entity_type") == "case_evidence"
            and _string(row, "evidence_ref") == reference
        ]
        if (
            not isinstance(reference, str)
            or reference not in current_refs
            or type(revision) is not int
            or revision < 1
            or len(sources) != 1
            or _string(sources[0], "user_id") != user_id
            or _integer(sources[0], "revision") != revision
        ):
            raise ValueError("Previous local source changed")
    selected = requested_actions[0]
    parameters = selected.get("parameters")
    source_ref = (
        parameters.get("source_ref") if isinstance(parameters, Mapping) else None
    )
    if (
        not isinstance(source_ref, str)
        or source_ref not in revisions
        or len(source_ref) > 256
        or selected.get("required_scopes") != []
        or selected.get("reversible") is not True
    ):
        raise ValueError("Previous local preparation source is invalid")
    actions = plan.get("actions")
    if not isinstance(actions, list) or len(actions) != (1 if status == "READY" else 0):
        raise ValueError("Previous local preparation actions are invalid")
    material = {
        "case_id": case_id,
        "version": version,
        "evidence_revisions": revisions,
        "actions": [
            {
                key: action[key]
                for key in (
                    "connector",
                    "target",
                    "verb",
                    "parameters",
                    "required_scopes",
                    "risk",
                    "reversible",
                )
            }
            for action in actions
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
    if digest != plan["hash"]:
        raise ValueError("Previous local preparation material changed")
    previous = {
        "status": status,
        "artifact_type": "NONE",
        "title": "",
        "content": "",
        "question": "",
        "explanation": plan.get("reason"),
        "source_ref": source_ref,
    }
    if status == "READY":
        action = actions[0]
        parameters = action.get("parameters")
        if (
            action.get("connector") != "quietpilot"
            or action.get("verb") != selected["verb"]
            or action.get("target") != selected.get("target_resource")
            or action.get("required_scopes") != []
            or action.get("reversible") is not True
            or action.get("status") != "SUCCEEDED"
            or action.get("verified") is not False
            or not isinstance(action.get("action_id"), str)
            or not action["action_id"]
            or action.get("result_ref")
            != f"local-preparation:{case_id}:{version}:{action['action_id']}"
            or action.get("html_url") is not None
            or not isinstance(parameters, dict)
            or set(parameters) != {"title", "content", "artifact_type", "source_ref"}
            or parameters.get("source_ref") != source_ref
            or parameters.get("artifact_type") != LOCAL_ARTIFACTS[selected["verb"]][0]
        ):
            raise ValueError("Previous local artifact is invalid")
        previous.update(
            {key: parameters[key] for key in ("title", "content", "artifact_type")}
        )
    for key, limit in {"title": 100, "content": 1200, "explanation": 240}.items():
        if not isinstance(previous[key], str) or len(previous[key]) > limit:
            raise ValueError("Previous local artifact copy is invalid")
    if not previous["explanation"].strip() or (
        status == "READY"
        and (not previous["title"].strip() or not previous["content"].strip())
    ):
        raise ValueError("Previous local artifact copy is missing")
    return previous


def _capability_records(
    user_id: str,
    capability_ids: list[object],
) -> list[dict[str, object]]:
    operations = {
        "quietpilot.reminder.prepare": "quietpilot.prepare_reminder",
        "quietpilot.task.prepare": "quietpilot.prepare_task",
        "quietpilot.reply.prepare": "quietpilot.prepare_reply",
        "google.calendar.events.create": "google.calendar_event_create",
    }
    records: list[dict[str, object]] = []
    for value in capability_ids:
        if not isinstance(value, str) or value not in operations:
            raise TypeError("Case capability is unsupported")
        records.append(
            {
                "user_id": user_id,
                "capability_id": value,
                "connector": "google"
                if value == "google.calendar.events.create"
                else "quietpilot",
                "status": "AVAILABLE",
                "operations": [operations[value]],
                "required_scopes": [CALENDAR_SCOPE]
                if value == "google.calendar.events.create"
                else [],
            }
        )
    return records


def _plan_from_result(
    context: Mapping[str, object], result: Mapping[str, object]
) -> tuple[dict[str, object], str, str]:
    if result.get("error_code") in {
        "CALENDAR_PREPARATION_FAILED",
        "CASE_PREPARATION_FAILED",
    }:
        plan, _, _ = _fallback_plan(context)
        summary = (
            "Could not verify the event plan. No event was created."
            if result["error_code"] == "CALENDAR_PREPARATION_FAILED"
            else "Could not verify the task plan. No external changes were made."
        )
        plan["reason"], plan["available_grant_modes"] = summary, []
        return plan, summary, "Prepare the plan again from the same source?"
    if result.get("error_code") in {
        "GOOGLE_CASE_SOURCE_UNAVAILABLE",
        "CALENDAR_DETAILS_REQUIRED",
    }:
        plan, _, _ = _fallback_plan(context)
        summary = (
            "Could not reopen the selected email. Check the connection and source."
            if result["error_code"] == "GOOGLE_CASE_SOURCE_UNAVAILABLE"
            else "The source does not confirm the event’s date and time."
        )
        question = (
            "Check the connection and prepare this task again?"
            if result["error_code"] == "GOOGLE_CASE_SOURCE_UNAVAILABLE"
            else "What date, start and end times, and time zone should the event use?"
        )
        plan["reason"] = summary
        plan["available_grant_modes"] = []
        return plan, summary, question
    try:
        local = _local_preparation(context, result)
    except (TypeError, ValueError, KeyError):
        plan, _, _ = _fallback_plan(context)
        explanation = "Could not verify that the draft matches your request."
        plan["reason"], plan["available_grant_modes"] = explanation, []
        return plan, explanation, "Try preparing again from the same source?"
    output = result.get("output")
    committed = result.get("committed") is True
    external_mutations = result.get("external_mutation_count")
    if not committed or external_mutations != 0 or not isinstance(output, Mapping):
        return _fallback_plan(context)
    if output.get("case_type") != context.get("case_type"):
        raise TypeError("Agent changed the Case type")
    goal = output.get("goal")
    explanation = output.get("explanation")
    decision_question = output.get("decision_question")
    revisions = output.get("evidence_revisions")
    raw_actions = output.get("actions")
    if (
        goal != context.get("goal")
        or not isinstance(explanation, str)
        or not explanation
        or not isinstance(decision_question, str)
        or not decision_question
        or not isinstance(revisions, Mapping)
        or not isinstance(raw_actions, list)
        or len(raw_actions) > MAX_ACTIONS
    ):
        raise TypeError("Agent returned an invalid Case plan")
    expected_revisions = {
        str(item["ref"]): int(item["revision"])
        for item in context["evidence"]  # type: ignore[index]
    }
    if dict(revisions) != expected_revisions:
        raise TypeError("Agent changed the Case evidence revisions")
    actions = [
        _action_from_result(context, item, index)
        for index, item in enumerate(raw_actions)
    ]
    risk = max(
        [str(context["risk"]), *(str(action["risk"]) for action in actions)],
        key={"LOW": 0, "MEDIUM": 1, "HIGH": 2}.__getitem__,
    )
    required_scopes = list(
        dict.fromkeys(
            str(scope)
            for action in actions
            for scope in action["required_scopes"]  # type: ignore[index]
        )
    )
    material = {
        "case_id": context["case_id"],
        "version": context["plan_version"],
        "evidence_revisions": expected_revisions,
        "actions": [
            {
                key: action[key]
                for key in (
                    "connector",
                    "target",
                    "verb",
                    "parameters",
                    "required_scopes",
                    "risk",
                    "reversible",
                )
            }
            for action in actions
        ],
        "risk": risk,
    }
    plan_hash = hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    plan = {
        "version": context["plan_version"],
        "hash": plan_hash,
        "evidence_revisions": expected_revisions,
        "reason": explanation,
        "expected_outcome": str(goal),
        "risk": risk,
        "reversibility": _reversibility(actions),
        "required_scopes": required_scopes,
        "available_grant_modes": ["ONCE"]
        if len(actions) == 1 and actions[0]["verb"] == "calendar_event_create"
        else [],
        "actions": actions,
    }
    if local is not None:
        plan["local_preparation_status"] = local["status"]
        plan["reason"] = explanation = local["explanation"]
        plan["available_grant_modes"] = []
        if local["status"] == "READY":
            action = actions[0]
            action.update(
                status="SUCCEEDED",
                result_summary=local["explanation"],
                result_ref=f"local-preparation:{context['case_id']}:{context['plan_version']}:{action['action_id']}",
                verified=False,
            )
            decision_question = {
                "REPLY_DRAFT": "Reply draft ready. No email was sent.",
                "CHECKLIST": "Checklist ready. No external changes were made.",
                "REMINDER": "Reminder text ready. No notification was scheduled.",
            }[local["artifact_type"]]
        elif local["status"] == "NO_ACTION":
            decision_question = (
                "No further preparation is needed. You can review the reason."
            )
        else:
            decision_question = local["question"]
    return plan, explanation, decision_question


def _local_preparation(
    context: Mapping[str, object], result: Mapping[str, object]
) -> dict[str, str] | None:
    if "local_preparation" not in result:
        return None
    marker = result["local_preparation"]
    limits = {
        "status": 11,
        "artifact_type": 11,
        "title": 100,
        "content": 1200,
        "question": 180,
        "explanation": 240,
        "source_ref": 256,
    }
    if (
        not isinstance(marker, dict)
        or set(marker) != set(limits)
        or any(
            not isinstance(marker[key], str) or len(marker[key]) > limit
            for key, limit in limits.items()
        )
        or marker["status"] not in {"READY", "NO_ACTION", "NEEDS_INPUT"}
        or not marker["explanation"].strip()
        or result.get("committed") is not True
        or type(result.get("external_mutation_count")) is not int
        or result["external_mutation_count"] != 0
    ):
        raise ValueError("Local preparation marker is invalid")
    selected = context.get("requested_actions")
    owner = context.get("user_id")
    evidence = context.get("evidence")
    if (
        not isinstance(selected, list)
        or len(selected) != 1
        or not isinstance(selected[0], Mapping)
        or not isinstance(owner, str)
        or not owner
        or not isinstance(evidence, list)
        or not 1 <= len(evidence) <= MAX_EVIDENCE
    ):
        raise ValueError("Local preparation was not selected by this owner")
    selected = selected[0]
    verb = selected.get("verb")
    expected = LOCAL_ARTIFACTS.get(verb) if isinstance(verb, str) else None
    if (
        selected.get("connector") != "quietpilot"
        or expected is None
        or selected.get("required_scopes") != []
        or selected.get("reversible") is not True
        or expected[1] not in context.get("capability_ids", [])
        or not isinstance(selected.get("parameters"), Mapping)
        or selected["parameters"].get("source_ref") != marker["source_ref"]
    ):
        raise ValueError("Local preparation changed the selected action")
    revisions: dict[str, int] = {}
    for record in evidence:
        if (
            not isinstance(record, Mapping)
            or record.get("user_id") != owner
            or not isinstance(record.get("ref"), str)
            or not record["ref"]
            or record["ref"] in revisions
            or type(record.get("revision")) is not int
            or record["revision"] < 1
        ):
            raise ValueError("Local preparation evidence is invalid")
        revisions[record["ref"]] = record["revision"]
    if marker["source_ref"] not in revisions:
        raise ValueError("Local preparation source changed")
    output = result.get("output")
    if (
        not isinstance(output, Mapping)
        or output.get("case_type") != context.get("case_type")
        or output.get("goal") != context.get("goal")
        or output.get("evidence_revisions") != revisions
        or any(
            type(value) is not int for value in output["evidence_revisions"].values()
        )
        or not isinstance(output.get("explanation"), str)
        or not output["explanation"].strip()
        or not isinstance(output.get("decision_question"), str)
        or not output["decision_question"].strip()
    ):
        raise ValueError("Local preparation output changed its Case")
    for key in ("title", "content", "question", "explanation"):
        if marker["source_ref"] in marker[key]:
            raise ValueError("Local preparation exposes an internal source reference")
        compact = "".join(marker[key].split()).casefold()
        for record in evidence:
            source = record.get("untrusted_text")
            if (
                record.get("source") == "gmail"
                and isinstance(source, str)
                and len(source.strip()) >= 40
                and "".join(source.split()).casefold() in compact
            ):
                raise ValueError("Local preparation copied the source body")
    actions = output.get("actions")
    if marker["status"] == "READY":
        if (
            marker["artifact_type"] != expected[0]
            or not marker["title"].strip()
            or not marker["content"].strip()
            or marker["content"].strip() == marker["title"].strip()
            or marker["question"]
            or not isinstance(actions, list)
            or len(actions) != 1
        ):
            raise ValueError("Local artifact is incomplete")
        action = actions[0]
        if (
            not isinstance(action, Mapping)
            or set(action)
            != {
                "connector",
                "verb",
                "target_resource",
                "parameters",
                "required_scopes",
                "risk",
                "reversible",
                "verification_method",
            }
            or action.get("connector") != "quietpilot"
            or action.get("verb") != verb
            or not isinstance(action.get("target_resource"), str)
            or not action["target_resource"]
            or action["target_resource"] != selected.get("target_resource")
            or action.get("parameters")
            != {
                key: marker[key]
                for key in ("source_ref", "title", "content", "artifact_type")
            }
            or action.get("required_scopes") != []
            or action.get("reversible") is not True
            or not isinstance(action.get("verification_method"), str)
            or not action["verification_method"]
            or action.get("risk") not in RISKS
        ):
            raise ValueError("Local artifact does not match the committed action")
    elif (
        actions != []
        or marker["artifact_type"] != "NONE"
        or marker["content"]
        or output["explanation"] != marker["explanation"]
    ):
        raise ValueError("Actionless preparation returned an artifact")
    elif marker["status"] == "NO_ACTION":
        if marker["title"] or marker["question"]:
            raise ValueError("No-action preparation is incomplete")
    elif (
        not marker["question"].strip().endswith(("?", "？"))
        or marker["question"].count("?") + marker["question"].count("？") != 1
        or "\n" in marker["question"]
        or output["decision_question"] != marker["question"]
    ):
        raise ValueError("Local preparation needs one specific question")
    return dict(marker)


def _action_from_result(
    context: Mapping[str, object], value: object, index: int
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("Agent Action is invalid")
    connector = value.get("connector")
    target = value.get("target_resource")
    verb = value.get("verb")
    parameters = value.get("parameters")
    scopes = value.get("required_scopes")
    risk = value.get("risk")
    reversible = value.get("reversible")
    if (
        not isinstance(connector, str)
        or not isinstance(target, str)
        or not isinstance(verb, str)
        or not isinstance(parameters, Mapping)
        or not isinstance(scopes, list)
        or any(not isinstance(scope, str) for scope in scopes)
        or risk not in RISKS
        or type(reversible) is not bool
    ):
        raise TypeError("Agent Action fields are invalid")
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    action_id = hashlib.sha256(
        f"{context['case_id']}:{index}:{canonical}".encode()
    ).hexdigest()[:24]
    return {
        "action_id": f"action-{action_id}",
        "connector": connector,
        "label": _action_label(verb),
        "target": target,
        "verb": verb,
        "parameters": dict(parameters),
        "risk": risk,
        "reversible": reversible,
        "required_scopes": scopes,
        "status": "PROPOSED",
        "result_summary": None,
    }


def _action_label(verb: str) -> str:
    return {
        "prepare_reminder": "Prepare reminder text",
        "prepare_task": "Prepare checklist",
        "prepare_reply": "Prepare reply draft",
        "calendar_event_create": "Add event to Google Calendar",
    }.get(verb, "Prepare next step")


def _fallback_plan(
    context: Mapping[str, object],
) -> tuple[dict[str, object], str, str]:
    summary = "The plan needs review. No external action was taken."
    question = "Review the source and prepare the plan again?"
    material = {
        "case_id": context["case_id"],
        "version": context["plan_version"],
        "evidence_revisions": {
            str(item["ref"]): int(item["revision"])
            for item in context["evidence"]  # type: ignore[index]
        },
        "actions": [],
        "risk": context["risk"],
    }
    plan_hash = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return (
        {
            "version": context["plan_version"],
            "hash": plan_hash,
            "reason": summary,
            "expected_outcome": str(context["goal"]),
            "risk": str(context["risk"]),
            "reversibility": "No external change was proposed or made.",
            "required_scopes": [],
            "available_grant_modes": ["ONCE"],
            "actions": [],
        },
        summary,
        question,
    )


def _reversibility(actions: list[dict[str, object]]) -> str:
    if not actions:
        return "No external change is proposed."
    if all(action["reversible"] is True for action in actions):
        return "Each listed action can be reversed after checking its result."
    return "Some actions are difficult to undo and need approval before execution."


def _string(item: Mapping[str, Any], name: str) -> str | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("S"), str):
        return value["S"]
    return None


def _required_string(item: Mapping[str, Any], name: str) -> str:
    value = _string(item, name)
    if value is None:
        raise TypeError(f"Case {name} is invalid")
    return value


def _integer(item: Mapping[str, Any], name: str) -> int | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("N"), str):
        return int(value["N"])
    return None


def _string_list(item: Mapping[str, Any], name: str) -> list[str]:
    value = item.get(name)
    raw = value.get("L", []) if isinstance(value, Mapping) else []
    if not isinstance(raw, list):
        raise TypeError(f"Case {name} is invalid")
    result: list[str] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("S"), str):
            raise TypeError(f"Case {name} is invalid")
        result.append(entry["S"])
    return result


def _json_actions(item: Mapping[str, Any], name: str) -> list[dict[str, object]]:
    encoded = _string(item, name)
    if encoded is None:
        return []
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise TypeError(f"Case {name} is invalid") from error
    if (
        not isinstance(value, list)
        or len(value) > MAX_ACTIONS
        or any(not isinstance(action, dict) for action in value)
    ):
        raise TypeError(f"Case {name} is invalid")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    nested = response.get("Error")
    return str(nested.get("Code")) if isinstance(nested, Mapping) else None


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value
