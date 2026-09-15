from __future__ import annotations

import copy
import json

import boto3
import pytest
from quietpilot_control_api.workspace_records import _case_evidence_item, _case_item
from quietpilot_worker.case_jobs import DynamoCasePreparationStore

moto = pytest.importorskip("moto")
TABLE = "case-notification-producers"
OWNER = "owner"
CASE = "case-one"
NOW = "2026-09-15T00:00:00Z"


@pytest.fixture
def environment():
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
        db.create_table(
            TableName=TABLE,
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
        meta = _case_item(
            user_id=OWNER,
            case_id=CASE,
            case_type="DIRECT_DELEGATION",
            goal="합성 작업",
            summary="합성 설명",
            risk="LOW",
            providers=[],
            evidence_refs=["direct:one"],
            now=NOW,
        )
        meta["version"] = {"N": "7"}
        db.put_item(TableName=TABLE, Item=meta)
        db.put_item(
            TableName=TABLE,
            Item=_case_evidence_item(
                user_id=OWNER,
                case_id=CASE,
                evidence_id="evidence-one",
                evidence_ref="direct:one",
                provider="direct",
                label="합성 근거",
                detail="합성 설명",
                source="direct",
                title="PRIVATE_SYNTHETIC_SOURCE",
                facts=[],
                untrusted_text="synthetic only",
                now=NOW,
            ),
        )
        plan = {
            "version": 1,
            "hash": "a" * 64,
            "risk": "LOW",
            "actions": [],
            "evidence_revisions": {"direct:one": 1},
        }
        yield db, DynamoCasePreparationStore(TABLE, db), plan


def read(db, suffix="META"):
    return db.get_item(
        TableName=TABLE,
        Key={"PK": {"S": f"CASE#{CASE}"}, "SK": {"S": suffix}},
        ConsistentRead=True,
    ).get("Item", {})


def notifications(db):
    return db.query(
        TableName=TABLE,
        KeyConditionExpression="PK=:pk AND begins_with(SK,:prefix)",
        ExpressionAttributeValues={
            ":pk": {"S": f"USER#{OWNER}"},
            ":prefix": {"S": "NOTIFICATION#"},
        },
        ConsistentRead=True,
    )["Items"]


def write(store, plan, owner=OWNER):
    store.write_plan(
        owner,
        CASE,
        plan["version"],
        plan=plan,
        summary="合成 description",
        decision_question="어떻게 진행할까요?",
    )


@pytest.mark.parametrize("local_status", [None, "NEEDS_INPUT"])
def test_decision_plan_atomically_enqueues_resulting_case_version_once(
    environment, local_status
):
    db, store, plan = environment
    if local_status:
        plan["local_preparation_status"] = local_status
    write(store, plan)
    meta = read(db)
    events = notifications(db)
    assert meta["status"] == {"S": "DECISION_REQUIRED"}
    assert meta["version"] == {"N": "8"}
    assert len(events) == 1 and events[0]["version"] == meta["version"]
    assert events[0]["kind"] == {"S": "DECISION_REQUIRED"}
    assert events[0]["user_id"] == {"S": OWNER}
    assert events[0]["case_id"] == {"S": CASE}
    assert "PRIVATE_SYNTHETIC_SOURCE" not in json.dumps(events)
    write(store, plan)
    assert notifications(db) == events and read(db) == meta


@pytest.mark.parametrize("local_status", ["READY", "NO_ACTION"])
def test_completed_local_preparation_is_silent(environment, local_status):
    db, store, plan = environment
    plan["local_preparation_status"] = local_status
    write(store, plan)
    assert read(db)["status"] == {"S": "COMPLETED"}
    assert read(db)["version"] == {"N": "8"}
    assert read(db, "PLAN#000001")
    assert notifications(db) == []


@pytest.mark.parametrize("change", ["risk", "evidence", "action", "copy_only"])
def test_material_change_ignores_version_and_copy_but_binds_new_case_version(
    environment, change
):
    db, store, plan = environment
    previous = copy.deepcopy(plan)
    db.put_item(
        TableName=TABLE,
        Item={
            "PK": {"S": f"CASE#{CASE}"},
            "SK": {"S": "PLAN#000001"},
            "plan_json": {"S": json.dumps(previous)},
            "version": {"N": "1"},
        },
    )
    meta = read(db)
    meta.update(
        requested_plan_version={"N": "2"},
        current_plan_version={"N": "1"},
        current_plan_hash={"S": previous["hash"]},
    )
    db.put_item(TableName=TABLE, Item=meta)
    plan.update(version=2, hash="b" * 64, reason="새 표시 문구")
    if change == "risk":
        plan["risk"] = "MEDIUM"
    elif change == "evidence":
        plan["evidence_revisions"] = {"direct:one": 2}
    elif change == "action":
        plan["actions"] = [{"connector": "quietpilot", "verb": "prepare_task"}]
    write(store, plan)
    event = notifications(db)[0]
    assert event["kind"] == {
        "S": "DECISION_REQUIRED" if change == "copy_only" else "PLAN_CHANGED"
    }
    assert event["version"] == read(db)["version"] == {"N": "8"}


@pytest.mark.parametrize("race", ["stop", "version", "new_plan"])
def test_case_race_blocks_plan_and_outbox_together(environment, monkeypatch, race):
    db, store, plan = environment
    transact = db.transact_write_items

    def change_before_commit(**kwargs):
        meta = read(db)
        meta["version"] = {"N": "8"}
        if race == "stop":
            meta["status"] = {"S": "STOPPED"}
        if race == "new_plan":
            meta["requested_plan_version"] = {"N": "2"}
        db.put_item(TableName=TABLE, Item=meta)
        return transact(**kwargs)

    monkeypatch.setattr(db, "transact_write_items", change_before_commit)
    if race == "version":
        with pytest.raises(db.exceptions.TransactionCanceledException):
            write(store, plan)
    else:
        write(store, plan)
    assert read(db)["version"] == {"N": "8"}
    assert not read(db, "PLAN#000001")
    assert notifications(db) == []


def test_outbox_condition_failure_rolls_back_case_plan_and_messages(
    environment, monkeypatch
):
    db, store, plan = environment
    transact = db.transact_write_items

    def reject_outbox(**kwargs):
        for operation in kwargs["TransactItems"]:
            put = operation.get("Put", {})
            if put.get("Item", {}).get("entity_type") == {"S": "notification"}:
                put["ConditionExpression"] = "attribute_exists(PK)"
        return transact(**kwargs)

    monkeypatch.setattr(db, "transact_write_items", reject_outbox)
    with pytest.raises(db.exceptions.TransactionCanceledException):
        write(store, plan)
    assert read(db)["status"] == {"S": "PREPARING"}
    assert read(db)["version"] == {"N": "7"}
    assert not read(db, "PLAN#000001") and notifications(db) == []
    assert db.scan(TableName=TABLE)["Count"] == 2


def test_other_owner_cannot_create_plan_or_notification(environment):
    db, store, plan = environment
    write(store, plan, owner="other-owner")
    assert read(db)["status"] == {"S": "PREPARING"}
    assert not read(db, "PLAN#000001") and notifications(db) == []
