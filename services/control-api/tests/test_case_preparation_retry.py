"""Offline Dynamo/SQS proof; run with ephemeral moto[dynamodb,sqs]."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import boto3
import pytest
from quietpilot_control_api.calendar_approval import CalendarApprovalService
from quietpilot_control_api.workspace import (
    DynamoWorkspaceStore,
    SqsCasePreparationQueue,
    WorkspaceConflict,
    WorkspaceNotFound,
    WorkspaceNotReady,
    WorkspaceService,
)
from quietpilot_control_api.workspace_records import _case_evidence_item, _case_item
from quietpilot_worker.case_jobs import (
    CALENDAR_SCOPE,
    CaseJobProcessor,
    DynamoCasePreparationStore,
)

moto = pytest.importorskip("moto", reason="Requires ephemeral Moto for Dynamo/SQS")
TABLE = "case-preparation-retry"
OWNER, CASE, REF = "synthetic-owner", "case-retry", "gmail:synthetic-source"
META_KEY = {"PK": {"S": f"CASE#{CASE}"}, "SK": {"S": "META"}}
NOW = "2026-09-14T00:00:00Z"


class Runtime:
    """Inference is synthetic; storage, queue serialization and processor are real."""

    def __init__(self, error_code=None):
        self.error_code = error_code
        self.calls = []

    def invoke_payload(self, user_id, payload):
        self.calls.append((user_id, copy.deepcopy(payload)))
        if self.error_code:
            return {
                "error_code": self.error_code,
                "committed": False,
                "external_mutation_count": 0,
            }
        request = payload["request"]
        return {
            "committed": True,
            "external_mutation_count": 0,
            "output": {
                "case_type": request["case_type"],
                "goal": request["goal"],
                "explanation": "기존 근거에서 일정 준비를 마쳤어요.",
                "decision_question": "이 개인 일정을 한 번 등록할까요?",
                "evidence_revisions": {
                    item["ref"]: item["revision"] for item in payload["evidence"]
                },
                "actions": [
                    {
                        "connector": "google",
                        "verb": "calendar_event_create",
                        "target_resource": "primary",
                        "parameters": {
                            "summary": "합성 검토 일정",
                            "start": "2030-10-07T09:00:00+09:00",
                            "end": "2030-10-07T10:00:00+09:00",
                            "source_ref": REF,
                        },
                        "required_scopes": [CALENDAR_SCOPE],
                        "risk": "MEDIUM",
                        "reversible": True,
                        "verification_method": "calendar_event_readback",
                    }
                ],
            },
        }


def envelope(version):
    return {
        "event_type": "DIRECT_REQUEST_RECEIVED",
        "user_id": OWNER,
        "connector": "google",
        "payload": {"case_id": CASE, "plan_version": version},
    }


def records(env):
    return {
        item["SK"]["S"]: item
        for item in env.db.query(
            TableName=TABLE,
            KeyConditionExpression="PK=:pk",
            ExpressionAttributeValues={":pk": {"S": f"CASE#{CASE}"}},
            ConsistentRead=True,
        )["Items"]
    }


def protected(env):
    return {
        key: value
        for key, value in records(env).items()
        if key.startswith("EVIDENCE#") or value.get("author", {}).get("S") == "USER"
    }


def receive(env):
    messages = env.sqs.receive_message(QueueUrl=env.url, MaxNumberOfMessages=10).get(
        "Messages", []
    )
    for message in messages:
        env.sqs.delete_message(QueueUrl=env.url, ReceiptHandle=message["ReceiptHandle"])
    return [json.loads(message["Body"]) for message in messages]


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
        sqs = boto3.client("sqs", region_name="us-east-1")
        db.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"} for name in ("PK", "SK")
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        url = sqs.create_queue(QueueName="case-preparation-retry")["QueueUrl"]
        meta = _case_item(
            user_id=OWNER,
            case_id=CASE,
            case_type="CONNECTED_SIGNAL",
            goal="합성 검토 일정 준비",
            summary="일정 준비",
            risk="MEDIUM",
            providers=["google"],
            evidence_refs=[REF],
            now=NOW,
        )
        meta["google_account_hash"] = {"S": "a" * 64}
        evidence = _case_evidence_item(
            user_id=OWNER,
            case_id=CASE,
            evidence_id="source",
            evidence_ref=REF,
            provider="google",
            label="검토 안내",
            detail="합성 일정 근거",
            source="gmail",
            title="합성 검토 안내",
            facts=["2030년 10월 7일 오전 9시부터 10시"],
            untrusted_text="합성 본문은 원래 근거에만 유지해요.",
            now=NOW,
        )
        user_message = {
            "PK": {"S": f"CASE#{CASE}"},
            "SK": {"S": "MESSAGE#original"},
            "entity_type": {"S": "message"},
            "message_id": {"S": "original-message"},
            "author": {"S": "USER"},
            "text": {"S": "기존 합성 사용자 요청"},
            "created_at": {"S": NOW},
        }
        for item in (meta, evidence, user_message):
            db.put_item(TableName=TABLE, Item=item)
        for connector in ("google", "google-calendar"):
            db.put_item(
                TableName=TABLE,
                Item={
                    "PK": {"S": f"USER#{OWNER}"},
                    "SK": {"S": f"CONNECTION#{connector}"},
                    "status": {"S": "CONNECTED"},
                    "mail_connection_id": {"S": "epoch"},
                    "account_hash": {"S": "a" * 64},
                    "granted_scopes": {"L": [{"S": CALENDAR_SCOPE}]},
                },
            )
        store = DynamoWorkspaceStore(TABLE, db)
        queue = SqsCasePreparationQueue(url, sqs)
        service = WorkspaceService(store, queue, CalendarApprovalService(store, queue))
        yield SimpleNamespace(
            db=db,
            sqs=sqs,
            url=url,
            store=store,
            service=service,
            processor_store=DynamoCasePreparationStore(TABLE, db),
        )


def failed_plan(env, code="CALENDAR_PREPARATION_FAILED"):
    runtime = Runtime(code)
    CaseJobProcessor(runtime, env.processor_store).process(envelope(1))
    return env.store.get_case(OWNER, CASE), runtime


def test_same_case_preparation_retry_crosses_sqs_and_worker_without_new_user_message(
    environment,
):
    env = environment
    original = protected(env)
    failed, first_runtime = failed_plan(env)
    old_plan = records(env)["PLAN#000001"]
    queued = env.service.retry(OWNER, case_id=CASE, expected_version=failed["version"])
    assert queued["status"] == "PREPARING"
    assert queued["case_id"] == CASE
    assert queued["requested_plan_version"] == 2
    assert queued["evidence"] == failed["evidence"]
    assert protected(env) == original
    [message] = receive(env)
    assert message["payload"] == {"case_id": CASE, "plan_version": 2}
    assert message["user_id"] == OWNER and message["connector"] == "google"
    assert message["dedupe_key"] == f"case-plan:{CASE}:2"
    for record in original.values():
        for key in ("untrusted_text", "text", "detail", "title"):
            if key in record:
                assert record[key]["S"] not in json.dumps(message, ensure_ascii=False)
    runtime = Runtime()
    processor = CaseJobProcessor(runtime, env.processor_store)
    processor.process(message)
    prepared = env.store.get_case(OWNER, CASE)
    assert prepared["status"] == "DECISION_REQUIRED"
    assert prepared["current_plan_version"] == prepared["requested_plan_version"] == 2
    assert prepared["plan"]["actions"][0]["status"] == "PROPOSED"
    assert prepared["plan"]["available_grant_modes"] == ["ONCE"]
    assert runtime.calls[0][1]["evidence"] == first_runtime.calls[0][1]["evidence"]
    assert protected(env) == original
    assert records(env)["PLAN#000001"] == old_plan
    assert not any(key.startswith(("ACTION#", "APPROVAL#")) for key in records(env))
    processor.process(message)
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("accepted_before_failure", [False, True])
def test_dispatch_failure_restores_decision_and_new_retry_supersedes_old_queue_job(
    environment, monkeypatch, accepted_before_failure
):
    env = environment
    failed, _ = failed_plan(env)
    original = protected(env)
    send = env.sqs.send_message

    def fail(**request):
        if accepted_before_failure:
            send(**request)
        raise TimeoutError("Synthetic SQS response failure")

    monkeypatch.setattr(env.sqs, "send_message", fail)
    with pytest.raises(TimeoutError):
        env.service.retry(OWNER, case_id=CASE, expected_version=failed["version"])
    restored = env.store.get_case(OWNER, CASE)
    assert restored["status"] == "DECISION_REQUIRED"
    assert (
        restored["current_plan_version"] == 1
        and restored["requested_plan_version"] == 2
    )
    assert "Could not start preparation" in restored["next_action"]
    assert protected(env) == original
    monkeypatch.setattr(env.sqs, "send_message", send)
    queued = env.service.retry(
        OWNER, case_id=CASE, expected_version=restored["version"]
    )
    assert queued["requested_plan_version"] == 3
    runtime = Runtime()
    processor = CaseJobProcessor(runtime, env.processor_store)
    processor.process(envelope(2))
    assert not runtime.calls
    for message in sorted(
        receive(env), key=lambda item: item["payload"]["plan_version"]
    ):
        processor.process(message)
    assert len(runtime.calls) == 1
    assert env.store.get_case(OWNER, CASE)["current_plan_version"] == 3
    assert protected(env) == original


@pytest.mark.parametrize(
    "guard",
    [
        "stale_version",
        "approved_operation",
        "COMPLETED",
        "RUNNING",
        "STOPPED",
        "other_owner",
    ],
)
def test_prior_approval_stale_owner_or_terminal_state_cannot_enter_preparation(
    environment, guard
):
    env = environment
    current, _ = failed_plan(env)
    if guard == "approved_operation":
        env.db.update_item(
            TableName=TABLE,
            Key=META_KEY,
            UpdateExpression="SET approved_operation_id=:value",
            ExpressionAttributeValues={":value": {"S": "prior-unknown-execution"}},
        )
    elif guard in {"COMPLETED", "RUNNING", "STOPPED"}:
        env.db.update_item(
            TableName=TABLE,
            Key=META_KEY,
            UpdateExpression="SET #status=:value",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":value": {"S": guard}},
        )
    before = records(env)
    with pytest.raises((WorkspaceConflict, WorkspaceNotFound, WorkspaceNotReady)):
        env.service.retry(
            "other-owner" if guard == "other_owner" else OWNER,
            case_id=CASE,
            expected_version=current["version"] - (guard == "stale_version"),
        )
    assert records(env) == before
    assert receive(env) == []


@pytest.mark.parametrize(
    "code,explanation",
    [
        ("CALENDAR_PREPARATION_FAILED", "Could not verify the event plan"),
        ("GOOGLE_CASE_SOURCE_UNAVAILABLE", "Could not reopen the selected email"),
        ("CALENDAR_DETAILS_REQUIRED", "does not confirm the event’s date and time"),
    ],
)
def test_calendar_runtime_failure_finishes_with_an_actionless_explained_decision(
    environment, code, explanation
):
    env = environment
    original = protected(env)
    current, runtime = failed_plan(env, code)
    assert current["status"] == "DECISION_REQUIRED"
    assert current["plan"]["actions"] == []
    assert current["plan"]["available_grant_modes"] == []
    assert explanation in current["summary"]
    assert current["plan"]["reason"] == current["summary"]
    assert current["next_action"]
    assert len(runtime.calls) == 1
    assert protected(env) == original
    assert receive(env) == []
