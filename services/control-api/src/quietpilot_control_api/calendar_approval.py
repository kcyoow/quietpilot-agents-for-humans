"""Persist an exact one-time Calendar approval before dispatching any work."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from .calendar_connection import CALENDAR_SCOPE
from .workspace import (
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceNotFound,
    WorkspaceNotReady,
)
from .workspace_records import _timeline_item


def calendar_plan_action(case: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Validate the frozen action, source revisions and canonical material hash."""
    actions = plan.get("actions")
    revisions = plan.get("evidence_revisions")
    expected = {e["evidence_ref"]: e["revision"] for e in case.get("evidence", [])}
    if (
        not isinstance(actions, list)
        or len(actions) != 1
        or not expected
        or revisions != expected
    ):
        raise WorkspaceNotReady("Prepare one source-grounded Calendar action first")
    action = actions[0]
    if (
        action.get("connector") != "google"
        or action.get("verb") != "calendar_event_create"
        or action.get("target") != "primary"
        or action.get("required_scopes") != [CALENDAR_SCOPE]
        or action.get("risk") not in {"MEDIUM", "HIGH"}
        or action.get("reversible") is not True
        or plan.get("risk") != action.get("risk")
        or plan.get("required_scopes") != [CALENDAR_SCOPE]
    ):
        raise WorkspaceNotReady("This action has no supported executor")
    parameters = action.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("source_ref") not in expected:
        raise WorkspaceInputError("Calendar source is invalid")
    if not {
        "summary",
        "start",
        "end",
        "source_ref",
    } <= parameters.keys() or parameters.keys() - {
        "summary",
        "start",
        "end",
        "source_ref",
        "description",
        "timeZone",
    }:
        raise WorkspaceInputError("Calendar fields are invalid")
    if (
        not all(isinstance(v, str) for v in parameters.values())
        or not parameters["summary"].strip()
    ):
        raise WorkspaceInputError("Calendar values are invalid")
    try:
        start = datetime.fromisoformat(parameters["start"])
        end = datetime.fromisoformat(parameters["end"])
        all_day = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", parameters["start"]))
        if all_day != bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", parameters["end"])):
            raise ValueError
        if not all_day and (start.tzinfo is None or end.tzinfo is None):
            raise ValueError
        if end <= start or end.replace(tzinfo=end.tzinfo or UTC) <= datetime.now(UTC):
            raise ValueError
    except (TypeError, ValueError):
        raise WorkspaceInputError(
            "Calendar dates need a future, unambiguous interval"
        ) from None
    material = {
        "case_id": case["case_id"],
        "version": plan["version"],
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
    if plan.get("hash") != digest:
        raise WorkspaceConflict("Plan material changed")
    # Execution projection never changes the immutable approved Action envelope.
    return {
        **{
            key: action[key]
            for key in (
                "action_id",
                "connector",
                "label",
                "target",
                "verb",
                "parameters",
                "required_scopes",
                "risk",
                "reversible",
            )
        },
        "status": "PROPOSED",
        "result_summary": None,
    }


class CalendarApprovalService:
    def __init__(self, workspace: Any, queue: Any):
        self.workspace = workspace
        self.client = workspace._client
        self.table = workspace._table_name
        self.queue = queue

    def approve(
        self,
        user_id: str,
        *,
        case_id: str,
        expected_version: int,
        plan_version: object,
        plan_hash: object,
        grant_mode: object,
    ) -> dict[str, object]:
        if (
            type(plan_version) is not int
            or plan_version < 1
            or not isinstance(plan_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", plan_hash)
        ):
            raise WorkspaceInputError("Approval must identify the exact current plan")
        if grant_mode != "ONCE":
            raise WorkspaceInputError("This executor requires one-time approval")
        case = self.workspace.get_case(user_id, case_id)
        if case is None:
            raise WorkspaceNotFound("Case not found")
        plan = case.get("plan")
        if (
            not isinstance(plan, dict)
            or plan.get("hash") != plan_hash
            or plan.get("version") != plan_version
            or case.get("current_plan_hash") != plan_hash
            or case.get("current_plan_version") != plan_version
        ):
            raise WorkspaceConflict("Plan changed before approval")
        operation_id = hashlib.sha256(
            f"{user_id}:{case_id}:{plan_version}:{plan_hash}".encode()
        ).hexdigest()
        approval_id = hashlib.sha256(
            f"{operation_id}:{expected_version}".encode()
        ).hexdigest()
        meta = self._meta(case_id)
        key = {"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": f"APPROVAL#{approval_id}"}}
        existing = self.client.get_item(
            TableName=self.table, Key=key, ConsistentRead=True
        ).get("Item")
        if existing:
            if (
                existing.get("expected_case_version", {}).get("N")
                != str(expected_version)
                or existing.get("user_id", {}).get("S") != user_id
                or meta.get("approved_approval_id", {}).get("S") != approval_id
            ):
                raise WorkspaceConflict("Approval replay does not match")
            if case["status"] == "QUEUED":
                self._dispatch(user_id, case_id, operation_id)
            return self.workspace.get_case(user_id, case_id)
        if case.get("version") != expected_version or case.get("status") not in {
            "DECISION_REQUIRED",
            "PAUSED",
            "PERMISSION_REVOKED",
        }:
            raise WorkspaceConflict("Case changed before approval")
        action = calendar_plan_action(case, plan)
        calendar_key = {
            "PK": {"S": f"USER#{user_id}"},
            "SK": {"S": "CONNECTION#google-calendar"},
        }
        calendar = self.client.get_item(
            TableName=self.table, Key=calendar_key, ConsistentRead=True
        ).get("Item", {})
        epoch = calendar.get("mail_connection_id", {}).get("S")
        account = calendar.get("account_hash", {}).get("S")
        if (
            calendar.get("status", {}).get("S") != "CONNECTED"
            or not epoch
            or not account
            or CALENDAR_SCOPE
            not in [
                item.get("S")
                for item in calendar.get("granted_scopes", {}).get("L", [])
            ]
        ):
            raise WorkspaceNotReady("Connect Calendar before approving this plan")
        if (
            "google" in case.get("providers", [])
            and meta.get("google_account_hash", {}).get("S") != account
        ):
            raise WorkspaceNotReady(
                "This mail Case belongs to an earlier Google account"
            )
        now = datetime.now(UTC)
        timestamp = now.isoformat().replace("+00:00", "Z")
        approval = {
            **key,
            "entity_type": {"S": "approval"},
            "user_id": {"S": user_id},
            "operation_id": {"S": operation_id},
            "approval_id": {"S": approval_id},
            "plan_hash": {"S": plan_hash},
            "plan_version": {"N": str(plan_version)},
            "expected_case_version": {"N": str(expected_version)},
            "grant_mode": {"S": "ONCE"},
            "decision": {"S": "APPROVE"},
            "expires_at": {"S": (now + timedelta(hours=1)).isoformat()},
            "created_at": {"S": timestamp},
            "mail_connection_id": {"S": epoch},
            "account_hash": {"S": account},
            "action_json": {
                "S": json.dumps(action, ensure_ascii=False, separators=(",", ":"))
            },
        }
        connection_condition = {
            "TableName": self.table,
            "ConditionExpression": "#status=:connected AND mail_connection_id=:epoch AND account_hash=:account",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {
                ":connected": {"S": "CONNECTED"},
                ":epoch": {"S": epoch},
                ":account": {"S": account},
            },
        }
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "ConditionCheck": {
                            **connection_condition,
                            "Key": calendar_key,
                            "ConditionExpression": connection_condition[
                                "ConditionExpression"
                            ]
                            + " AND contains(granted_scopes,:scope)",
                            "ExpressionAttributeValues": {
                                **connection_condition["ExpressionAttributeValues"],
                                ":scope": {"S": CALENDAR_SCOPE},
                            },
                        }
                    },
                    {
                        "ConditionCheck": {
                            **connection_condition,
                            "Key": {
                                "PK": {"S": f"USER#{user_id}"},
                                "SK": {"S": "CONNECTION#google"},
                            },
                        }
                    },
                    {
                        "ConditionCheck": {
                            "TableName": self.table,
                            "Key": {
                                "PK": {"S": f"CASE#{case_id}"},
                                "SK": {"S": f"PLAN#{plan_version:06d}"},
                            },
                            "ConditionExpression": "plan_hash=:hash",
                            "ExpressionAttributeValues": {":hash": {"S": plan_hash}},
                        }
                    },
                    {
                        "Update": {
                            "TableName": self.table,
                            "Key": {
                                "PK": {"S": f"CASE#{case_id}"},
                                "SK": {"S": "META"},
                            },
                            "UpdateExpression": "SET #status=:queued, approved_operation_id=:operation, approved_approval_id=:approval, next_action=:next, updated_at=:now, version=version+:one",
                            "ConditionExpression": "user_id=:user AND version=:version AND current_plan_hash=:hash AND current_plan_version=:plan AND (#status=:decision OR #status=:paused OR #status=:revoked)",
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": {
                                ":queued": {"S": "QUEUED"},
                                ":operation": {"S": operation_id},
                                ":approval": {"S": approval_id},
                                ":next": {
                                    "S": "Saving the approved event and checking the result."
                                },
                                ":now": {"S": timestamp},
                                ":one": {"N": "1"},
                                ":user": {"S": user_id},
                                ":version": {"N": str(expected_version)},
                                ":hash": {"S": plan_hash},
                                ":plan": {"N": str(plan_version)},
                                ":decision": {"S": "DECISION_REQUIRED"},
                                ":paused": {"S": "PAUSED"},
                                ":revoked": {"S": "PERMISSION_REVOKED"},
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table,
                            "Item": approval,
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table,
                            "Item": _timeline_item(
                                case_id,
                                label="Calendar approval",
                                body="Approved this exact event once.",
                                state="DONE",
                                now=timestamp,
                            ),
                        }
                    },
                ]
            )
        except Exception as error:
            if (
                getattr(error, "response", {}).get("Error", {}).get("Code")
                == "TransactionCanceledException"
            ):
                raise WorkspaceConflict(
                    "Plan, Case or connection changed before approval"
                ) from None
            raise
        self._dispatch(user_id, case_id, operation_id)
        return self.workspace.get_case(user_id, case_id)

    def _meta(self, case_id: str) -> dict[str, Any]:
        return self.client.get_item(
            TableName=self.table,
            Key={"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": "META"}},
            ConsistentRead=True,
        ).get("Item", {})

    def retry(
        self, user_id: str, *, case_id: str, expected_version: int
    ) -> dict[str, object]:
        meta = self._meta(case_id)
        if meta.get("user_id", {}).get("S") != user_id:
            raise WorkspaceNotFound("Case not found")
        operation_id = meta.get("approved_operation_id", {}).get("S")
        approval_id = meta.get("approved_approval_id", {}).get("S")
        if not operation_id or not approval_id:
            raise WorkspaceNotReady("There is no approved action to retry")
        approval = self.client.get_item(
            TableName=self.table,
            Key={
                "PK": {"S": f"CASE#{case_id}"},
                "SK": {"S": f"APPROVAL#{approval_id}"},
            },
            ConsistentRead=True,
        ).get("Item", {})
        expires = approval.get("expires_at", {}).get("S", "")
        if not expires or datetime.fromisoformat(expires) <= datetime.now(UTC):
            try:
                self.client.update_item(
                    TableName=self.table,
                    Key={"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": "META"}},
                    UpdateExpression="SET #status=:decision, next_action=:next, updated_at=:now, version=version+:one",
                    ConditionExpression="user_id=:user AND version=:version AND approved_operation_id=:operation AND approved_approval_id=:approval AND (#status=:failed OR #status=:verifying OR #status=:queued)",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":decision": {"S": "DECISION_REQUIRED"},
                        ":next": {
                            "S": "Approval expired. Review the event and approve it again."
                        },
                        ":now": {
                            "S": datetime.now(UTC).isoformat().replace("+00:00", "Z")
                        },
                        ":one": {"N": "1"},
                        ":user": {"S": user_id},
                        ":version": {"N": str(expected_version)},
                        ":operation": {"S": operation_id},
                        ":approval": {"S": approval_id},
                        ":failed": {"S": "FAILED"},
                        ":verifying": {"S": "VERIFYING"},
                        ":queued": {"S": "QUEUED"},
                    },
                )
            except Exception as error:
                if (
                    getattr(error, "response", {}).get("Error", {}).get("Code")
                    == "ConditionalCheckFailedException"
                ):
                    raise WorkspaceConflict(
                        "Execution state changed before retry"
                    ) from None
                raise
            return self.workspace.get_case(user_id, case_id)
        try:
            self.client.update_item(
                TableName=self.table,
                Key={"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": "META"}},
                UpdateExpression="SET #status=:queued, next_action=:next, updated_at=:now, version=version+:one",
                ConditionExpression="user_id=:user AND version=:version AND approved_operation_id=:operation AND approved_approval_id=:approval AND (#status=:failed OR #status=:verifying OR #status=:queued)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":queued": {"S": "QUEUED"},
                    ":next": {"S": "Checking whether this event was saved."},
                    ":now": {"S": datetime.now(UTC).isoformat().replace("+00:00", "Z")},
                    ":one": {"N": "1"},
                    ":user": {"S": user_id},
                    ":version": {"N": str(expected_version)},
                    ":operation": {"S": operation_id},
                    ":approval": {"S": approval_id},
                    ":failed": {"S": "FAILED"},
                    ":verifying": {"S": "VERIFYING"},
                },
            )
        except Exception as error:
            if (
                getattr(error, "response", {}).get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                raise WorkspaceConflict(
                    "Execution state changed before retry"
                ) from None
            raise
        self._dispatch(user_id, case_id, operation_id)
        return self.workspace.get_case(user_id, case_id)

    def _dispatch(self, user_id: str, case_id: str, operation_id: str) -> None:
        self.queue.send(
            user_id=user_id,
            event_type="ACTION_EXECUTE_REQUESTED",
            payload={"case_id": case_id, "operation_id": operation_id},
        )
