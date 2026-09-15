"""Offline routine service -> new validated mail -> preparation queue contract."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import boto3
import pytest
from quietpilot_control_api.calendar_connection import CALENDAR_SCOPE
from quietpilot_control_api.routines import (
    DEADLINE_DESCRIPTION,
    RoutineService,
)
from quietpilot_control_api.routines import (
    DynamoRoutineStore as ApiStore,
)
from quietpilot_control_api.workspace_records import _case_evidence_item, _case_item
from quietpilot_worker.case_jobs import DynamoCasePreparationStore
from quietpilot_worker.routines import (
    GMAIL_SCOPE,
    RoutineProcessor,
    SqsRoutineCaseQueue,
)
from quietpilot_worker.routines import (
    DynamoRoutineStore as WorkerStore,
)

moto = pytest.importorskip("moto")
TABLE, USER, CASE = "routine-flow", "routine-owner", "verified-calendar-case"
ACCOUNT = hashlib.sha256(b"synthetic-account@example.invalid").hexdigest()
EPOCH, SCAN = "mail-epoch-one", "a" * 32
NOW = datetime(2030, 1, 1, tzinfo=UTC)


def j(value):
    return {
        "S": json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    }


def key(pk, sk):
    return {"PK": {"S": pk}, "SK": {"S": sk}}


def put(env, item):
    env.db.put_item(TableName=TABLE, Item=item)


def get(env, pk, sk):
    return (
        env.db.get_item(TableName=TABLE, Key=key(pk, sk), ConsistentRead=True).get(
            "Item"
        )
        or {}
    )


def rows(env, pk):
    return env.db.query(
        TableName=TABLE,
        KeyConditionExpression="PK=:pk",
        ExpressionAttributeValues={":pk": {"S": pk}},
        ConsistentRead=True,
    )["Items"]


@pytest.fixture
def routine_env(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
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
            BillingMode="PAY_PER_REQUEST",
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
        )
        sqs = boto3.client("sqs", region_name="us-east-1")
        url = sqs.create_queue(QueueName="routine-preparation")["QueueUrl"]
        env = SimpleNamespace(db=db, sqs=sqs, url=url, now=NOW)
        env.api_store = ApiStore(TABLE, db)
        env.api = RoutineService(env.api_store, clock=lambda: env.now)
        env.worker_store = WorkerStore(TABLE, db)
        env.queue = SqsRoutineCaseQueue(url, sqs)
        env.processor = RoutineProcessor(
            env.worker_store, env.queue, clock=lambda: env.now
        )
        yield env


def seed_source(
    env, *, owner=USER, case_id=CASE, deadline=True, domain="sender.example.invalid"
):
    ref = "gmail:" + hashlib.sha256(f"{owner}:{case_id}:source".encode()).hexdigest()
    stamp = NOW.isoformat().replace("+00:00", "Z")
    params = {
        "summary": "ORIGINAL EVENT TITLE",
        "start": "2030-02-01T10:00:00+09:00",
        "end": "2030-02-01T10:15:00+09:00" if deadline else "2030-02-01T11:00:00+09:00",
        "source_ref": ref,
    }
    if deadline:
        params["description"] = DEADLINE_DESCRIPTION
    action = {
        "action_id": "calendar-action",
        "connector": "google",
        "target": "primary",
        "verb": "calendar_event_create",
        "parameters": params,
        "required_scopes": [CALENDAR_SCOPE],
        "risk": "MEDIUM",
        "reversible": True,
        "label": "원래 일정",
        "status": "PROPOSED",
        "result_summary": None,
    }
    evidence = _case_evidence_item(
        user_id=owner,
        case_id=case_id,
        evidence_id="source",
        evidence_ref=ref,
        provider="google",
        label="근거",
        detail="정규화된 근거",
        source="gmail",
        title="원래 메일",
        facts=[
            f"sender_domain={domain}",
            f"received_at_unix_ms={int((NOW - timedelta(days=1)).timestamp() * 1000)}",
        ],
        untrusted_text=None,
        now=stamp,
    )
    evidence["revision"] = {"N": "3"}
    material = {
        "case_id": case_id,
        "version": 1,
        "evidence_revisions": {ref: 3},
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
        "risk": "MEDIUM",
    }
    digest = hashlib.sha256(
        json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    plan = {
        **material,
        "actions": [action],
        "hash": digest,
        "required_scopes": [CALENDAR_SCOPE],
        "reason": "검증한 일정",
        "expected_outcome": "일정 등록",
        "reversibility": "삭제 가능",
        "available_grant_modes": ["ONCE"],
    }
    operation, approval_id = "operation-original", "approval-original"
    meta = _case_item(
        user_id=owner,
        case_id=case_id,
        case_type="CONNECTED_SIGNAL",
        goal="원래 일정 준비",
        summary="원래 요약",
        risk="MEDIUM",
        providers=["google"],
        evidence_refs=[ref],
        now=stamp,
    )
    meta.update(
        status={"S": "COMPLETED"},
        version={"N": "7"},
        current_plan_version={"N": "1"},
        current_plan_hash={"S": digest},
        approved_operation_id={"S": operation},
        approved_approval_id={"S": approval_id},
        google_account_hash={"S": ACCOUNT},
        mail_connection_id={"S": EPOCH},
        GSI1PK={"S": f"USER#{owner}#CASE#HISTORY"},
    )
    execution = {
        **key(f"CASE#{case_id}", f"ACTION#{operation}"),
        "entity_type": {"S": "action_execution"},
        "user_id": {"S": owner},
        "operation_id": {"S": operation},
        "approval_id": {"S": approval_id},
        "action_id": {"S": "calendar-action"},
        "plan_version": {"N": "1"},
        "plan_hash": {"S": digest},
        "status": {"S": "SUCCEEDED"},
        "verified": {"BOOL": True},
        "result_ref": {
            "S": "google-calendar:primary:qp"
            + hashlib.sha256(
                json.dumps(
                    {"operation": "calendar.event_create.v1", "id": operation},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        },
    }
    approval = {
        **key(f"CASE#{case_id}", f"APPROVAL#{approval_id}"),
        "entity_type": {"S": "approval"},
        "user_id": {"S": owner},
        "operation_id": {"S": operation},
        "approval_id": {"S": approval_id},
        "plan_version": {"N": "1"},
        "plan_hash": {"S": digest},
        "decision": {"S": "APPROVE"},
        "grant_mode": {"S": "ONCE"},
        "mail_connection_id": {"S": EPOCH},
        "account_hash": {"S": ACCOUNT},
    }
    profile = {
        "tags": ["학교"],
        "description": "학교 일정",
        "version": 1,
        "updated_at": stamp,
    }
    for item in (
        meta,
        evidence,
        execution,
        approval,
        {**key(f"CASE#{case_id}", "PLAN#000001"), "plan_json": j(plan)},
        {
            **key(f"USER#{owner}", "CONNECTION#google"),
            "status": {"S": "CONNECTED"},
            "mail_connection_id": {"S": EPOCH},
            "account_hash": {"S": ACCOUNT},
            "granted_scopes": {"L": [{"S": GMAIL_SCOPE}]},
        },
        {
            **key(f"USER#{owner}", "MAIL_INTERESTS#google"),
            "profile_json": j(profile),
            "profile_version": {"N": "1"},
            "scan_id": {"S": SCAN},
            "scan_connection_id": {"S": EPOCH},
            "scan_json": j({"scan_id": SCAN, "profile_version": 1, "status": "READY"}),
        },
    ):
        put(env, item)
    return ref


def activate(env, *, case_id=CASE, owner=USER):
    proposal = env.api.propose(owner, case_id=case_id, expected_version=7)["routine"]
    return env.api.activate(
        owner, routine_id=proposal["routine_id"], expected_version=proposal["version"]
    )["routine"]


def seed_candidate(
    env,
    *,
    name="new",
    owner=USER,
    received=None,
    domain="sender.example.invalid",
    kind="DEADLINE",
    verb=None,
):
    reference = "gmail:" + hashlib.sha256(f"{owner}:{name}".encode()).hexdigest()
    candidate_id = hashlib.sha256(f"candidate:{owner}:{name}".encode()).hexdigest()[:32]
    group = {
        "DEADLINE": "deadlines",
        "APPOINTMENT": "appointments",
        "FOLLOW_UP": "follow-ups",
    }[kind]
    verb = verb or ("prepare_task" if kind == "DEADLINE" else "prepare_reminder")
    capability = (
        "quietpilot.task.prepare"
        if verb == "prepare_task"
        else "quietpilot.reminder.prepare"
    )
    received = received if received is not None else env.now - timedelta(seconds=1)
    stamp = env.now.isoformat().replace("+00:00", "Z")
    action = {
        "connector": "quietpilot",
        "target_resource": f"case:{kind.lower()}",
        "verb": verb,
        "parameters": {"source_ref": reference, "title": "새 메일의 작업 준비"},
        "required_scopes": [],
        "risk": "LOW",
        "reversible": True,
        "verification_method": "case_plan_readback",
    }
    evidence = {
        **key(f"USER#{owner}", f"EVIDENCE#{reference}"),
        "entity_type": {"S": "evidence"},
        "user_id": {"S": owner},
        "evidence_ref": {"S": reference},
        "revision": {"N": "1"},
        "source": {"S": "gmail"},
        "title": {"S": "새로운 합성 메일"},
        "facts": {
            "L": [
                {"S": f"sender_domain={domain}"},
                {"S": f"received_at_unix_ms={int(received.timestamp() * 1000)}"},
                {"S": "source_content=body"},
            ]
        },
        "untrusted_text": {"S": "PRIVATE SYNTHETIC BODY MUST NOT BE COPIED"},
    }
    candidate = {
        **key(f"USER#{owner}", f"CANDIDATE#{candidate_id}"),
        "entity_type": {"S": "candidate"},
        "user_id": {"S": owner},
        "candidate_id": {"S": candidate_id},
        "source_type": {"S": "CONNECTED_SIGNAL"},
        "status": {"S": "VISIBLE"},
        "version": {"N": "1"},
        "confidence": {"N": "0.9"},
        "outcome": {"S": "새 메일의 작업 준비"},
        "summary": {"S": "새 메일에서 검증된 일정 관련 정보"},
        "why_now": {"S": "받은 요청 확인"},
        "risk": {"S": "LOW"},
        "opportunity_type": {"S": kind},
        "primary_group_id": {"S": group},
        "evidence_refs": {"L": [{"S": reference}]},
        "required_capabilities": {"L": [{"S": capability}]},
        "proposed_actions_json": j([action]),
        "mail_profile_version": {"N": "1"},
        "mail_scan_id": {"S": SCAN},
        "mail_connection_id": {"S": EPOCH},
        "created_at": {"S": stamp},
        "updated_at": {"S": stamp},
        "GSI1PK": {"S": f"USER#{owner}#CANDIDATE#VISIBLE#{group}"},
        "GSI1SK": {"S": f"{stamp}#{candidate_id}"},
    }
    put(env, evidence)
    put(env, candidate)
    return candidate_id, reference


def messages(env):
    return [
        json.loads(item["Body"])
        for item in env.sqs.receive_message(
            QueueUrl=env.url, MaxNumberOfMessages=10
        ).get("Messages", [])
    ]


def test_inactive_proposal_then_explicit_activation_and_new_mail_create_one_preparation_case(
    routine_env,
):
    env = routine_env
    seed_source(env)
    proposed = env.api.propose(USER, case_id=CASE, expected_version=7)["routine"]
    assert proposed["status"] == proposed["effective_status"] == "PROPOSED"
    old_id, _ = seed_candidate(env, name="old")
    assert env.processor.process(USER)["created"] == 0
    assert not messages(env)
    active = env.api.activate(
        USER, routine_id=proposed["routine_id"], expected_version=1
    )["routine"]
    env.now += timedelta(minutes=1)
    candidate_id, reference = seed_candidate(env)
    stats = env.processor.process(USER)
    assert stats["created"] == stats["dispatched"] == 1 and not stats["limit_reached"]
    queued = messages(env)
    assert len(queued) == 1
    event = queued[0]
    assert (
        event["event_type"] == "DIRECT_REQUEST_RECEIVED"
        and event["user_id"] == USER
        and event["connector"] == "google"
    )
    assert event["dedupe_key"] == f"case-plan:{event['payload']['case_id']}:1"
    case_id = event["payload"]["case_id"]
    meta = get(env, f"CASE#{case_id}", "META")
    assert meta["case_type"] == {"S": "ROUTINE_DISCOVERY"} and meta["status"] == {
        "S": "PREPARING"
    }
    assert meta["origin_routine_id"] == {"S": active["routine_id"]}
    assert meta["routine_mode"] == {"S": "PREPARE_ONLY"} and meta[
        "dispatch_pending"
    ] == {"BOOL": False}
    context = DynamoCasePreparationStore(TABLE, env.db).load(USER, case_id, 1)
    assert context["requested_actions"][0]["parameters"]["source_ref"] == reference
    assert context["requested_actions"][0]["required_scopes"] == []
    assert context["evidence"][0]["untrusted_text"] is None
    assert "PRIVATE SYNTHETIC BODY" not in json.dumps(rows(env, f"CASE#{case_id}"))
    assert "ORIGINAL EVENT TITLE" not in json.dumps(rows(env, f"CASE#{case_id}"))
    assert get(env, f"USER#{USER}", f"CANDIDATE#{candidate_id}")["status"] == {
        "S": "CONVERTED"
    }
    assert get(env, f"USER#{USER}", f"CANDIDATE#{old_id}")["status"] == {"S": "VISIBLE"}
    assert env.processor.process(USER)["created"] == 0


def test_multiple_matching_active_rules_consume_one_candidate_once(routine_env):
    env = routine_env
    seed_source(env)
    seed_source(env, case_id="second-verified-case")
    activate(env)
    activate(env, case_id="second-verified-case")
    env.now += timedelta(minutes=1)
    seed_candidate(env)
    assert env.processor.process(USER)["created"] == 1
    assert len(messages(env)) == 1
    assert env.processor.process(USER)["created"] == 0
