"""Refine only verified local results; exercise actual Dynamo/SQS boundaries."""

from __future__ import annotations

import json
from types import SimpleNamespace

import boto3
import pytest
from quietpilot_control_api.workspace import (
    DynamoWorkspaceStore,
    SqsCasePreparationQueue,
    WorkspaceConflict,
    WorkspaceNotFound,
    WorkspaceService,
)
from quietpilot_control_api.workspace_records import _case_evidence_item, _case_item
from quietpilot_worker.case_jobs import (
    CaseJobProcessor,
    DynamoCasePreparationStore,
    _plan_from_result,
)

from services.worker.tests.test_local_preparation_jobs import (
    CASE,
    SOURCE,
    USER,
    context,
    result,
)

moto = pytest.importorskip("moto")
TABLE = "local-refinement"
NOW = "2026-09-14T00:00:00Z"
META = {"PK": {"S": f"CASE#{CASE}"}, "SK": {"S": "META"}}


@pytest.fixture
def env(monkeypatch):
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
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("PK", "SK", "GSI1PK", "GSI1SK")
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        url = sqs.create_queue(QueueName="local-refinement")["QueueUrl"]
        store = DynamoWorkspaceStore(TABLE, db)
        yield SimpleNamespace(
            db=db,
            sqs=sqs,
            url=url,
            store=store,
            service=WorkspaceService(store, SqsCasePreparationQueue(url, sqs)),
            worker=DynamoCasePreparationStore(TABLE, db),
        )


def seed(env, status="READY", verb="prepare_reply"):
    source = context(verb)
    meta = _case_item(
        user_id=USER,
        case_id=CASE,
        case_type=source["case_type"],
        goal=source["goal"],
        summary="내용 준비",
        risk=source["risk"],
        providers=["google"],
        evidence_refs=[SOURCE],
        now=NOW,
    )
    meta.update(
        required_capabilities={
            "L": [{"S": value} for value in source["capability_ids"]]
        },
        requested_actions_json={
            "S": json.dumps(source["requested_actions"], ensure_ascii=False)
        },
    )
    evidence = _case_evidence_item(
        user_id=USER,
        case_id=CASE,
        evidence_id="source",
        evidence_ref=SOURCE,
        provider="google",
        label="합성 행사 안내",
        detail="원래 합성 근거",
        source="gmail",
        title="행사 안내",
        facts=["합성 행사 안내"],
        untrusted_text="원래 합성 본문",
        now=NOW,
    )
    evidence["revision"] = {"N": "3"}
    for item in (meta, evidence):
        env.db.put_item(TableName=TABLE, Item=item)
    plan, summary, question = _plan_from_result(source, result(source, status))
    env.worker.write_plan(
        USER, CASE, 1, plan=plan, summary=summary, decision_question=question
    )
    return source


def rows(env):
    return {
        item["SK"]["S"]: item
        for item in env.db.query(
            TableName=TABLE,
            KeyConditionExpression="PK=:pk",
            ExpressionAttributeValues={":pk": {"S": f"CASE#{CASE}"}},
            ConsistentRead=True,
        )["Items"]
    }


def post(
    env,
    version,
    *,
    text="조금 더 짧게 정리해 주세요.",
    key="local-refinement-one",
    user=USER,
):
    return env.service.post_message(
        user, case_id=CASE, text=text, expected_version=version, idempotency_key=key
    )


def messages(env):
    found = env.sqs.receive_message(QueueUrl=env.url, MaxNumberOfMessages=10).get(
        "Messages", []
    )
    for item in found:
        env.sqs.delete_message(QueueUrl=env.url, ReceiptHandle=item["ReceiptHandle"])
    return [json.loads(item["Body"]) for item in found]


@pytest.mark.parametrize(
    "status,verb",
    [
        ("READY", "prepare_reply"),
        ("READY", "prepare_task"),
        ("READY", "prepare_reminder"),
        ("NO_ACTION", "prepare_reply"),
    ],
)
def test_local_completion_refines_in_same_case_preserving_evidence_and_old_plan(
    env, status, verb
):
    source = seed(env, status, verb)
    before = rows(env)
    current = env.store.get_case(USER, CASE)
    assert current["status"] == "COMPLETED"
    assert env.store.list_cases(USER, "active") == []
    response = post(env, current["version"])
    assert response["case"]["status"] == "PREPARING"
    assert response["case"]["case_id"] == CASE
    assert response["case"]["requested_plan_version"] == 2
    assert [item["case_id"] for item in env.store.list_cases(USER, "active")] == [CASE]
    assert env.store.list_cases(USER, "history") == []
    assert rows(env)["EVIDENCE#source"] == before["EVIDENCE#source"]
    assert rows(env)["PLAN#000001"] == before["PLAN#000001"]
    calls = []

    def invoke(user_id, invocation):
        assert user_id == USER
        calls.append(invocation)
        updated = {**source, "evidence": invocation["evidence"]}
        value = result(updated)
        value["output"]["evidence_revisions"] = {
            item["ref"]: item["revision"] for item in invocation["evidence"]
        }
        value["local_preparation"]["content"] = "안내 감사합니다."
        value["output"]["actions"][0]["parameters"]["content"] = "안내 감사합니다."
        return value

    processor = CaseJobProcessor(SimpleNamespace(invoke_payload=invoke), env.worker)
    [message] = messages(env)
    processor.process(message)
    processor.process(message)
    assert len(calls) == 1
    assert any(
        item["source"] == "direct"
        and item["untrusted_text"] == "조금 더 짧게 정리해 주세요."
        for item in calls[0]["evidence"]
    )
    final = env.store.get_case(USER, CASE)
    assert final["status"] == "COMPLETED" and final["current_plan_version"] == 2
    assert final["actions"][0]["parameters"]["content"] == "안내 감사합니다."
    assert rows(env)["PLAN#000001"] == before["PLAN#000001"]
    assert rows(env)["EVIDENCE#source"] == before["EVIDENCE#source"]
    assert env.store.list_cases(USER, "active") == []
    assert len([item for item in final["messages"] if item["author"] == "USER"]) == 1


def test_duplicate_request_is_idempotent_and_next_pending_message_supersedes_generation(
    env,
):
    seed(env)
    version = env.store.get_case(USER, CASE)["version"]
    first = post(env, version)
    saved = rows(env)
    replay = post(env, version)
    assert replay["message_id"] == first["message_id"]
    assert rows(env) == saved
    second = post(
        env,
        first["case"]["version"],
        key="local-refinement-two",
        text="마지막 문장도 간단히 해 주세요.",
    )
    assert second["case"]["requested_plan_version"] == 3
    assert (
        len([item for item in second["case"]["messages"] if item["author"] == "USER"])
        == 2
    )
    queued = messages(env)
    assert [item["payload"]["plan_version"] for item in queued] == [2, 2, 3]
    assert queued[0]["dedupe_key"] == queued[1]["dedupe_key"] != queued[2]["dedupe_key"]
    assert env.worker.load(USER, CASE, 2) is None


@pytest.mark.parametrize(
    "local_status,change",
    [
        (status, change)
        for status in ("READY", "NO_ACTION")
        for change in (
            "stale",
            "other_owner",
            "approved_pointer",
            "orphan_execution",
            "plan_pointer",
            "missing_marker",
            "APPROVED",
            "QUEUED",
            "RUNNING",
            "VERIFYING",
            "STOPPED",
            "PERMISSION_REVOKED",
        )
    ]
    + [("READY", "external_action"), ("READY", "scopes")],
)
def test_only_authoritative_local_completion_without_execution_can_be_refined(
    env, change, local_status
):
    seed(env, local_status)
    version = env.store.get_case(USER, CASE)["version"]
    current = rows(env)
    meta = current["META"]
    if change == "approved_pointer":
        meta["approved_operation_id"] = {"S": "existing-calendar-operation"}
    elif change == "plan_pointer":
        meta["current_plan_hash"] = {"S": "c" * 64}
    elif change in {
        "APPROVED",
        "QUEUED",
        "RUNNING",
        "VERIFYING",
        "STOPPED",
        "PERMISSION_REVOKED",
    }:
        meta["status"] = {"S": change}
    env.db.put_item(TableName=TABLE, Item=meta)
    if change == "orphan_execution":
        env.db.put_item(
            TableName=TABLE,
            Item={
                "PK": {"S": f"CASE#{CASE}"},
                "SK": {"S": "ACTION#old"},
                "entity_type": {"S": "action_execution"},
                "status": {"S": "VERIFYING"},
                "user_id": {"S": USER},
            },
        )
    if change in {"missing_marker", "external_action", "scopes"}:
        record = current["PLAN#000001"]
        plan = json.loads(record["plan_json"]["S"])
        if change == "missing_marker":
            del plan["local_preparation_status"]
        elif change == "external_action":
            plan["actions"][0].update(connector="google", verb="calendar_event_create")
        else:
            plan["actions"][0]["required_scopes"] = ["calendar.events.owned"]
        record["plan_json"]["S"] = json.dumps(plan)
        env.db.put_item(TableName=TABLE, Item=record)
    before = rows(env)
    with pytest.raises((WorkspaceConflict, WorkspaceNotFound)):
        post(
            env,
            version - (change == "stale"),
            user="other-owner" if change == "other_owner" else USER,
        )
    assert rows(env) == before
    assert messages(env) == []


@pytest.mark.parametrize("change", ["approval", "plan"])
def test_concurrent_approval_or_plan_change_cannot_commit_a_refinement(
    env, monkeypatch, change
):
    seed(env, "NO_ACTION")
    version = env.store.get_case(USER, CASE)["version"]
    original = env.db.transact_write_items
    injected = []

    def interleave(**request):
        if not injected:
            injected.append(True)
            if change == "approval":
                env.db.update_item(
                    TableName=TABLE,
                    Key=META,
                    UpdateExpression="SET approved_operation_id=:operation",
                    ExpressionAttributeValues={
                        ":operation": {"S": "concurrent-calendar-operation"}
                    },
                )
            else:
                record = rows(env)["PLAN#000001"]
                plan = json.loads(record["plan_json"]["S"])
                plan["local_preparation_status"] = "NEEDS_INPUT"
                record["plan_json"]["S"] = json.dumps(plan)
                env.db.put_item(TableName=TABLE, Item=record)
        return original(**request)

    monkeypatch.setattr(env.db, "transact_write_items", interleave)
    with pytest.raises(WorkspaceConflict):
        post(env, version)
    after = rows(env)
    assert after["META"]["status"]["S"] == "COMPLETED"
    assert after["META"]["version"]["N"] == str(version)
    assert after["META"]["GSI1PK"]["S"].endswith("#HISTORY")
    assert not any(item.get("author", {}).get("S") == "USER" for item in after.values())
    assert messages(env) == []
