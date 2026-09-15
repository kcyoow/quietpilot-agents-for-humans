from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import boto3
import pytest
from quietpilot_control_api.calendar_approval import CalendarApprovalService
from quietpilot_control_api.calendar_connection import CALENDAR_SCOPE
from quietpilot_control_api.workspace import (
    DynamoWorkspaceStore,
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceNotReady,
)
from quietpilot_control_api.workspace_records import _case_evidence_item, _case_item
from quietpilot_worker.case_jobs import DynamoCasePreparationStore, _plan_from_result

moto = pytest.importorskip("moto")


@pytest.fixture
def approval_environment():
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
        db.create_table(
            TableName="cases",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        now = "2026-09-14T00:00:00Z"
        meta = _case_item(
            user_id="owner",
            case_id="case-one",
            case_type="CONNECTED_SIGNAL",
            goal="약속을 캘린더에 등록",
            summary="약속 준비",
            risk="MEDIUM",
            providers=["google"],
            evidence_refs=["gmail:fixture"],
            now=now,
        )
        meta["google_account_hash"] = {"S": "a" * 64}
        meta["mail_connection_id"] = {"S": "epoch-one"}
        evidence = _case_evidence_item(
            user_id="owner",
            case_id="case-one",
            evidence_id="e-one",
            evidence_ref="gmail:fixture",
            provider="google",
            label="예약 안내",
            detail="정규화된 일정 근거",
            source="gmail",
            title="예약 안내",
            facts=["예약 확인"],
            untrusted_text=None,
            now=now,
        )
        for item in [meta, evidence]:
            db.put_item(TableName="cases", Item=item)
        for suffix in ["google", "google-calendar"]:
            db.put_item(
                TableName="cases",
                Item={
                    "PK": {"S": "USER#owner"},
                    "SK": {"S": f"CONNECTION#{suffix}"},
                    "status": {"S": "CONNECTED"},
                    "mail_connection_id": {"S": "epoch-one"},
                    "account_hash": {"S": "a" * 64},
                    "granted_scopes": {"L": [{"S": CALENDAR_SCOPE}]},
                },
            )
        context = {
            "case_id": "case-one",
            "case_type": "CONNECTED_SIGNAL",
            "plan_version": 1,
            "goal": "약속을 캘린더에 등록",
            "risk": "MEDIUM",
            "evidence": [{"ref": "gmail:fixture", "revision": 1}],
        }
        action = {
            "connector": "google",
            "verb": "calendar_event_create",
            "target_resource": "primary",
            "parameters": {
                "summary": "예약",
                "start": "2030-05-01T10:00:00+09:00",
                "end": "2030-05-01T11:00:00+09:00",
                "source_ref": "gmail:fixture",
            },
            "required_scopes": [CALENDAR_SCOPE],
            "risk": "MEDIUM",
            "reversible": True,
            "verification_method": "calendar_event_readback",
        }
        result = {
            "committed": True,
            "external_mutation_count": 0,
            "output": {
                "case_type": context["case_type"],
                "goal": context["goal"],
                "explanation": "예약을 확인했어요.",
                "decision_question": "이 일정을 등록할까요?",
                "evidence_revisions": {"gmail:fixture": 1},
                "actions": [action],
            },
        }
        plan, summary, question = _plan_from_result(context, result)
        DynamoCasePreparationStore("cases", db).write_plan(
            "owner",
            "case-one",
            1,
            plan=plan,
            summary=summary,
            decision_question=question,
        )
        store = DynamoWorkspaceStore("cases", db)
        calls = []
        service = CalendarApprovalService(
            store, SimpleNamespace(send=lambda **kw: calls.append(kw))
        )
        args = {
            "case_id": "case-one",
            "expected_version": 2,
            "plan_version": 1,
            "plan_hash": plan["hash"],
            "grant_mode": "ONCE",
        }
        yield db, store, service, calls, args


def test_approval_persists_exact_plan_before_dispatch_and_replays_safely(
    approval_environment,
):
    db, store, service, calls, args = approval_environment
    original_plan = copy.deepcopy(store.get_case("owner", "case-one")["plan"])
    result = service.approve("owner", **args)
    assert result["status"] == "QUEUED" and result["version"] == 3
    assert result["plan"] == original_plan
    assert len(calls) == 1 and calls[0]["event_type"] == "ACTION_EXECUTE_REQUESTED"
    operation = calls[0]["payload"]["operation_id"]
    approval_id = hashlib.sha256(f"{operation}:2".encode()).hexdigest()
    approval = db.get_item(
        TableName="cases",
        Key={"PK": {"S": "CASE#case-one"}, "SK": {"S": f"APPROVAL#{approval_id}"}},
    )["Item"]
    assert approval["plan_hash"]["S"] == args["plan_hash"]
    assert approval["grant_mode"]["S"] == "ONCE"
    assert service.approve("owner", **args)["version"] == 3
    assert calls[0]["payload"] == calls[1]["payload"]


@pytest.mark.parametrize(
    "change",
    [
        "hash",
        "version",
        "grant",
        "connection",
        "evidence",
        "parameters",
        "scope",
        "stopped",
    ],
)
def test_stale_or_unsupported_approval_never_dispatches(approval_environment, change):
    db, store, service, calls, args = approval_environment
    if change == "hash":
        args["plan_hash"] = "f" * 64
    elif change == "version":
        args["expected_version"] = 1
    elif change == "grant":
        args["grant_mode"] = "STANDING"
    elif change == "connection":
        db.update_item(
            TableName="cases",
            Key={"PK": {"S": "USER#owner"}, "SK": {"S": "CONNECTION#google"}},
            UpdateExpression="SET mail_connection_id=:new",
            ExpressionAttributeValues={":new": {"S": "epoch-two"}},
        )
    elif change == "evidence":
        db.update_item(
            TableName="cases",
            Key={"PK": {"S": "CASE#case-one"}, "SK": {"S": "EVIDENCE#e-one"}},
            UpdateExpression="SET revision=:new",
            ExpressionAttributeValues={":new": {"N": "2"}},
        )
    elif change in {"parameters", "scope"}:
        plan = store.get_case("owner", "case-one")["plan"]
        if change == "parameters":
            plan["actions"][0]["parameters"]["summary"] = "Different outcome"
        else:
            plan["actions"][0]["required_scopes"] = []
        db.update_item(
            TableName="cases",
            Key={"PK": {"S": "CASE#case-one"}, "SK": {"S": "PLAN#000001"}},
            UpdateExpression="SET plan_json=:plan",
            ExpressionAttributeValues={":plan": {"S": json.dumps(plan)}},
        )
    else:
        db.update_item(
            TableName="cases",
            Key={"PK": {"S": "CASE#case-one"}, "SK": {"S": "META"}},
            UpdateExpression="SET #status=:stopped",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":stopped": {"S": "STOPPED"}},
        )
    with pytest.raises((WorkspaceConflict, WorkspaceInputError, WorkspaceNotReady)):
        service.approve("owner", **args)
    assert calls == []


def test_queue_failure_keeps_replayable_approval_without_second_authorization(
    approval_environment,
):
    _db, store, service, calls, args = approval_environment

    def fail(**kw):
        raise RuntimeError("queue unavailable")

    service.queue = SimpleNamespace(send=fail)
    with pytest.raises(RuntimeError, match="queue"):
        service.approve("owner", **args)
    assert store.get_case("owner", "case-one")["status"] == "QUEUED"
    service.queue = SimpleNamespace(send=lambda **kw: calls.append(kw))
    service.approve("owner", **args)
    assert len(calls) == 1


def test_new_explicit_approval_reuses_operation_without_overwriting_old_approval(
    approval_environment,
):
    db, _store, service, calls, args = approval_environment
    service.approve("owner", **args)
    operation = calls[0]["payload"]["operation_id"]
    old_id = hashlib.sha256(f"{operation}:2".encode()).hexdigest()
    key = {"PK": {"S": "CASE#case-one"}, "SK": {"S": "META"}}
    db.update_item(
        TableName="cases",
        Key=key,
        UpdateExpression="SET #status=:status, version=:version",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": {"S": "DECISION_REQUIRED"},
            ":version": {"N": "4"},
        },
    )
    result = service.approve("owner", **{**args, "expected_version": 4})
    assert result["status"] == "QUEUED" and result["version"] == 5
    assert calls[1]["payload"]["operation_id"] == operation
    assert (
        db.get_item(
            TableName="cases",
            Key={"PK": {"S": "CASE#case-one"}, "SK": {"S": f"APPROVAL#{old_id}"}},
        )["Item"]["expected_case_version"]["N"]
        == "2"
    )
    with pytest.raises(WorkspaceConflict):
        service.approve("owner", **args)


def test_retry_dispatches_same_operation_and_rejects_stale_retry(approval_environment):
    _db, _store, service, calls, args = approval_environment
    service.approve("owner", **args)
    result = service.retry("owner", case_id="case-one", expected_version=3)
    assert result["status"] == "QUEUED" and result["version"] == 4
    assert calls[0]["payload"] == calls[1]["payload"]
    with pytest.raises(WorkspaceConflict):
        service.retry("owner", case_id="case-one", expected_version=3)
