"""API-to-Worker regressions for expired approval and unresolved Calendar results."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from quietpilot_control_api import calendar_approval
from quietpilot_control_api.workspace import WorkspaceConflict, WorkspaceService
from quietpilot_worker.calendar_execution import (
    GMAIL_SCOPE,
    CalendarExecutionProcessor,
    CalendarExecutionRetry,
    DynamoCalendarExecutionStore,
)

from .test_calendar_approval import approval_environment as approval_fixture


class Recovery:
    def __init__(self, environment: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch):
        self.db, self.store, self.approvals, self.dispatches, self.approve_args = (
            environment
        )
        self.now = datetime.now(UTC)
        owner = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return owner.now.astimezone(tz or UTC)

        monkeypatch.setattr(calendar_approval, "datetime", Clock)
        # The approval-only fixture uses one scope for both records; the real
        # executor also requires the existing Gmail read consent.
        self.db.update_item(
            TableName="cases",
            Key={"PK": {"S": "USER#owner"}, "SK": {"S": "CONNECTION#google"}},
            UpdateExpression="SET granted_scopes=:scopes",
            ExpressionAttributeValues={":scopes": {"L": [{"S": GMAIL_SCOPE}]}},
        )
        self.runtime_calls: list[dict[str, object]] = []
        self.response: dict[str, object] = {
            "status": "VERIFYING",
            "verified": False,
            "error_code": "CALENDAR_RESULT_UNCONFIRMED",
            "result_ref": None,
            "html_url": None,
        }
        runtime = SimpleNamespace(invoke_payload=self.invoke)
        self.worker = CalendarExecutionProcessor(
            runtime,
            DynamoCalendarExecutionStore(
                "cases", self.db, clock=lambda: self.now.timestamp()
            ),
        )
        self.planning_calls: list[dict[str, object]] = []
        self.workspace = WorkspaceService(
            self.store,
            SimpleNamespace(send=lambda **payload: self.planning_calls.append(payload)),
            execution=self.approvals,
        )

    def invoke(self, user_id: str, payload: dict[str, object]) -> dict[str, object]:
        assert user_id == "owner"
        self.runtime_calls.append(copy.deepcopy(payload))
        return copy.deepcopy(self.response)

    def case(self) -> dict[str, Any]:
        return self.store.get_case("owner", "case-one")

    def item(self, suffix: str) -> dict[str, Any]:
        return self.db.get_item(
            TableName="cases",
            Key={"PK": {"S": "CASE#case-one"}, "SK": {"S": suffix}},
            ConsistentRead=True,
        ).get("Item", {})

    def dispatch_worker(self) -> None:
        self.worker.process(
            {
                "event_type": "ACTION_EXECUTE_REQUESTED",
                "connector": "google",
                "user_id": "owner",
                "payload": self.dispatches[-1]["payload"],
            }
        )

    def begin(self, status: str = "VERIFYING") -> None:
        if status == "FAILED":
            self.response.update(
                status="FAILED", error_code="CALENDAR_READBACK_MISMATCH"
            )
        self.approvals.approve("owner", **self.approve_args)
        if status == "VERIFYING":
            with pytest.raises(CalendarExecutionRetry):
                self.dispatch_worker()
        else:
            self.dispatch_worker()
        assert self.case()["status"] == status

    def complete_response(self, operation: str) -> None:
        material = {"operation": "calendar.event_create.v1", "id": operation}
        event_id = (
            "qp"
            + hashlib.sha256(
                json.dumps(
                    material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
        self.response = {
            "status": "COMPLETED",
            "verified": True,
            "error_code": None,
            "result_ref": f"google-calendar:primary:{event_id}",
            "html_url": "https://www.google.com/calendar/event?eid=offline-recovery",
        }

    def edit(self, key: str = "recovery-edit") -> dict[str, object]:
        return self.workspace.post_message(
            "owner",
            case_id="case-one",
            text=f"설명 수정 요청 {key}",
            expected_version=self.case()["version"],
            idempotency_key=key,
        )


@pytest.fixture
def recovery(monkeypatch: pytest.MonkeyPatch):
    environment = approval_fixture.__wrapped__()
    try:
        yield Recovery(next(environment), monkeypatch)
    finally:
        environment.close()


@pytest.mark.parametrize("previous_status", ["FAILED", "VERIFYING"])
def test_expired_execution_returns_to_approval_and_recovers_the_same_operation(
    recovery: Recovery, previous_status: str
) -> None:
    recovery.begin(previous_status)
    before = recovery.case()
    operation = recovery.dispatches[0]["payload"]["operation_id"]
    meta = recovery.item("META")
    previous_approval_id = meta["approved_approval_id"]["S"]
    previous_approval = recovery.item(f"APPROVAL#{previous_approval_id}")
    previous_action = recovery.item(f"ACTION#{operation}")
    original_plan = recovery.item("PLAN#000001")
    recovery.now += timedelta(hours=2)

    decision = recovery.workspace.retry(
        "owner", case_id="case-one", expected_version=before["version"]
    )
    assert decision["status"] == "DECISION_REQUIRED"
    assert decision["version"] == before["version"] + 1
    assert recovery.item(f"ACTION#{operation}") == previous_action
    assert len(recovery.dispatches) == len(recovery.runtime_calls) == 1
    with pytest.raises(WorkspaceConflict):
        recovery.workspace.retry(
            "owner", case_id="case-one", expected_version=before["version"]
        )

    approved = recovery.workspace.decide(
        "owner",
        case_id="case-one",
        decision="APPROVE",
        expected_version=decision["version"],
        plan_version=decision["plan"]["version"],
        plan_hash=decision["plan"]["hash"],
        grant_mode="ONCE",
    )
    assert approved["status"] == "QUEUED"
    assert recovery.dispatches[-1]["payload"] == recovery.dispatches[0]["payload"]
    assert recovery.item("META")["approved_approval_id"]["S"] != previous_approval_id
    assert recovery.item(f"APPROVAL#{previous_approval_id}") == previous_approval

    recovery.complete_response(operation)
    recovery.dispatch_worker()
    final = recovery.case()
    assert final["status"] == "COMPLETED"
    assert final["actions"][0]["status"] == "SUCCEEDED"
    assert final["actions"][0]["verified"] is True
    assert recovery.runtime_calls[0] == recovery.runtime_calls[1]
    assert recovery.item("PLAN#000001") == original_plan
    assert recovery.item(f"APPROVAL#{previous_approval_id}") == previous_approval


@pytest.mark.parametrize(
    "case_state",
    ["VERIFYING", "PAUSED", "DECISION_REQUIRED", "FAILED", "PERMISSION_REVOKED"],
)
def test_unresolved_calendar_action_cannot_branch_into_a_new_plan(
    recovery: Recovery, case_state: str
) -> None:
    recovery.begin()
    if case_state == "PAUSED":
        recovery.workspace.decide(
            "owner",
            case_id="case-one",
            decision="DEFER",
            expected_version=recovery.case()["version"],
        )
    elif case_state == "DECISION_REQUIRED":
        recovery.now += timedelta(hours=2)
        recovery.workspace.retry(
            "owner", case_id="case-one", expected_version=recovery.case()["version"]
        )
    elif case_state in {"FAILED", "PERMISSION_REVOKED"}:
        recovery.now += timedelta(seconds=151)
        recovery.workspace.retry(
            "owner", case_id="case-one", expected_version=recovery.case()["version"]
        )
        recovery.response.update(
            status="FAILED",
            error_code="GOOGLE_AUTH_REQUIRED"
            if case_state == "PERMISSION_REVOKED"
            else "CALENDAR_READBACK_MISMATCH",
        )
        recovery.dispatch_worker()
    assert recovery.case()["status"] == case_state
    operation = recovery.dispatches[0]["payload"]["operation_id"]
    previous_meta = recovery.item("META")
    previous_plan = recovery.item("PLAN#000001")
    previous_action = recovery.item(f"ACTION#{operation}")
    invocation_count = len(recovery.runtime_calls)
    dispatch_count = len(recovery.dispatches)

    with pytest.raises(WorkspaceConflict, match="existing action result"):
        recovery.edit()

    assert recovery.planning_calls == []
    assert recovery.item("PLAN#000002") == {}
    assert recovery.item("META") == previous_meta
    assert recovery.item("PLAN#000001") == previous_plan
    assert recovery.item(f"ACTION#{operation}") == previous_action
    assert len(recovery.runtime_calls) == invocation_count
    assert len(recovery.dispatches) == dispatch_count


@pytest.mark.parametrize("case_state", ["DECISION_REQUIRED", "PREPARING"])
def test_cases_without_execution_keep_their_existing_edit_flow(
    recovery: Recovery, case_state: str
) -> None:
    if case_state == "PREPARING":
        recovery.edit("first-edit")
    assert recovery.case()["status"] == case_state
    previous = recovery.case()["requested_plan_version"]
    result = recovery.edit("next-edit")
    assert result["case"]["status"] == "PREPARING"
    assert result["case"]["requested_plan_version"] == previous + 1
    assert recovery.runtime_calls == []
    assert recovery.dispatches == []
    assert recovery.planning_calls[-1]["plan_version"] == previous + 1
