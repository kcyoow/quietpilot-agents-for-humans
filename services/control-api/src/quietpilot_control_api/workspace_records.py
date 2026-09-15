"""DynamoDB Case records and provider-neutral mobile response projection."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

CASE_TYPES = frozenset(
    {
        "CONNECTED_SIGNAL",
        "DIRECT_DELEGATION",
        "ROUTINE_DISCOVERY",
        "EXCEPTION_APPROVAL",
    }
)
ACTIVE_STATUSES = frozenset(
    {
        "PREPARING",
        "DECISION_REQUIRED",
        "APPROVED",
        "QUEUED",
        "RUNNING",
        "VERIFYING",
        "FAILED",
        "PAUSED",
        "PERMISSION_REVOKED",
    }
)
HISTORY_STATUSES = frozenset({"COMPLETED", "STOPPED"})
RISKS = frozenset({"LOW", "MEDIUM", "HIGH"})


def _case_item(
    *,
    user_id: str,
    case_id: str,
    case_type: str,
    goal: str,
    summary: str,
    risk: str,
    providers: list[str],
    evidence_refs: list[str],
    now: str,
) -> dict[str, Any]:
    priority = 50
    return {
        "PK": {"S": f"CASE#{case_id}"},
        "SK": {"S": "META"},
        "entity_type": {"S": "case"},
        "case_id": {"S": case_id},
        "user_id": {"S": user_id},
        "case_type": {"S": case_type},
        "goal": {"S": goal},
        "summary": {"S": summary},
        "status": {"S": "PREPARING"},
        "risk": {"S": risk},
        "priority": {"N": str(priority)},
        "providers": {"L": [{"S": provider} for provider in providers]},
        "why_now": {"S": "Preparing a task from the new source."},
        "next_action": {"S": "Reviewing the source and required actions."},
        "evidence_refs": {"L": [{"S": ref} for ref in evidence_refs]},
        "requested_plan_version": {"N": "1"},
        "version": {"N": "1"},
        "created_at": {"S": now},
        "updated_at": {"S": now},
        "GSI1PK": {"S": f"USER#{user_id}#CASE#ACTIVE"},
        "GSI1SK": {"S": _case_sort_key(priority, now, case_id)},
    }


def _case_evidence_item(
    *,
    user_id: str,
    case_id: str,
    evidence_id: str,
    evidence_ref: str,
    provider: str,
    label: str,
    detail: str,
    source: str,
    title: str,
    facts: list[str],
    untrusted_text: str | None,
    now: str,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "PK": {"S": f"CASE#{case_id}"},
        "SK": {"S": f"EVIDENCE#{evidence_id}"},
        "entity_type": {"S": "case_evidence"},
        "user_id": {"S": user_id},
        "evidence_id": {"S": evidence_id},
        "evidence_ref": {"S": evidence_ref},
        "provider": {"S": provider},
        "label": {"S": label},
        "detail": {"S": detail},
        "revision": {"N": "1"},
        "source": {"S": source},
        "title": {"S": title},
        "facts": {"L": [{"S": fact} for fact in facts]},
        "created_at": {"S": now},
    }
    if untrusted_text is not None:
        item["untrusted_text"] = {"S": untrusted_text}
    return item


def _timeline_item(
    case_id: str,
    *,
    label: str,
    body: str,
    state: str,
    now: str,
) -> dict[str, Any]:
    event_id = str(uuid.uuid4())
    return {
        "PK": {"S": f"CASE#{case_id}"},
        "SK": {"S": f"EVENT#{now}#{event_id}"},
        "entity_type": {"S": "audit_event"},
        "event_id": {"S": event_id},
        "label": {"S": label},
        "body": {"S": body},
        "state": {"S": state},
        "occurred_at": {"S": now},
    }


def _case_summary_from_item(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("Case item is invalid")
    case_id = _string(value, "case_id")
    case_type = _string(value, "case_type")
    goal = _string(value, "goal")
    summary = _string(value, "summary")
    status = _string(value, "status")
    risk = _string(value, "risk")
    priority = _integer(value, "priority")
    version = _integer(value, "version")
    updated_at = _string(value, "updated_at")
    if (
        not case_id
        or case_type not in CASE_TYPES
        or not goal
        or not summary
        or status not in ACTIVE_STATUSES | HISTORY_STATUSES
        or risk not in RISKS
        or priority is None
        or version is None
        or version < 1
        or not updated_at
    ):
        raise TypeError("Case fields are invalid")
    return {
        "case_id": case_id,
        "case_type": case_type,
        "goal": goal,
        "summary": summary,
        "status": status,
        "risk": risk,
        "priority": priority,
        "version": version,
        "updated_at": updated_at,
        "why_now": _string(value, "why_now"),
        "next_action": _string(value, "next_action"),
        "providers": _string_list(value, "providers"),
    }


def _case_detail_from_items(
    meta: Mapping[str, Any], items: list[object]
) -> dict[str, object]:
    result = _case_summary_from_item(meta)
    plan = _latest_plan(items)
    if plan:
        executions = {
            _string(item, "action_id"): item
            for item in items
            if isinstance(item, Mapping)
            and _entity(item) == "action_execution"
            and _integer(item, "plan_version") == plan.get("version")
            and _string(item, "plan_hash") == plan.get("hash")
            and _string(item, "user_id") == _string(meta, "user_id")
            and _string(item, "operation_id") == _string(meta, "approved_operation_id")
        }
        for action in plan.get("actions", []):
            execution = executions.get(action.get("action_id"))
            if execution:
                action.update(
                    status=_string(execution, "status") or action["status"],
                    result_summary=_string(execution, "result_summary"),
                    result_ref=_string(execution, "result_ref"),
                    html_url=_string(execution, "html_url"),
                    verified=execution.get("verified", {}).get("BOOL") is True,
                    error_code=_string(execution, "error_code"),
                )
    result.update(
        {
            "why_now": _string(meta, "why_now") or "Review this task’s source.",
            "next_action": _string(meta, "next_action") or "Preparing a plan.",
            "providers": _string_list(meta, "providers"),
            "current_plan_version": _integer(meta, "current_plan_version"),
            "current_plan_hash": _string(meta, "current_plan_hash"),
            "requested_plan_version": _integer(meta, "requested_plan_version"),
            "evidence": sorted(
                (
                    _evidence_from_item(item)
                    for item in items
                    if _entity(item) == "case_evidence"
                ),
                key=lambda item: str(item["evidence_id"]),
            ),
            "plan": plan,
            "actions": (plan or {}).get("actions", []),
            "timeline": sorted(
                (
                    _timeline_from_item(item)
                    for item in items
                    if _entity(item) == "audit_event"
                ),
                key=lambda item: str(item["occurred_at"]),
            ),
            "messages": sorted(
                (
                    _message_from_item(item)
                    for item in items
                    if _entity(item) == "message"
                ),
                key=lambda item: str(item["created_at"]),
            ),
        }
    )
    return result


def _evidence_from_item(value: Mapping[str, Any]) -> dict[str, object]:
    return {
        "evidence_id": _required_string(value, "evidence_id"),
        "evidence_ref": _required_string(value, "evidence_ref"),
        "provider": _required_string(value, "provider"),
        "label": _required_string(value, "label"),
        "detail": _required_string(value, "detail"),
        "revision": _integer(value, "revision") or 1,
    }


def _timeline_from_item(value: Mapping[str, Any]) -> dict[str, object]:
    return {
        "event_id": _required_string(value, "event_id"),
        "label": _required_string(value, "label"),
        "body": _required_string(value, "body"),
        "state": _required_string(value, "state"),
        "occurred_at": _required_string(value, "occurred_at"),
    }


def _message_from_item(value: Mapping[str, Any]) -> dict[str, object]:
    return {
        "message_id": _required_string(value, "message_id"),
        "author": _required_string(value, "author"),
        "text": _required_string(value, "text"),
        "created_at": _required_string(value, "created_at"),
    }


def _latest_plan(items: list[object]) -> dict[str, object] | None:
    plans: list[tuple[int, dict[str, object]]] = []
    for item in items:
        if not isinstance(item, Mapping) or _entity(item) != "plan":
            continue
        version = _integer(item, "version")
        encoded = _string(item, "plan_json")
        if version is None or encoded is None:
            raise TypeError("Case plan item is invalid")
        value = json.loads(encoded)
        if not isinstance(value, dict):
            raise TypeError("Case plan body is invalid")
        plans.append((version, value))
    return max(plans, key=lambda item: item[0])[1] if plans else None


def _entity(value: object) -> str | None:
    return _string(value, "entity_type") if isinstance(value, Mapping) else None


def _case_key(case_id: str) -> dict[str, dict[str, str]]:
    return {"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": "META"}}


def _candidate_key(user_id: str, candidate_id: str) -> dict[str, dict[str, str]]:
    return {
        "PK": {"S": f"USER#{user_id}"},
        "SK": {"S": f"CANDIDATE#{candidate_id}"},
    }


def _case_sort_key(priority: int, now: str, case_id: str) -> str:
    return f"{999999 - priority:06d}#{now}#{case_id}"


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
