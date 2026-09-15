from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import boto3
import pytest
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from quietpilot_worker.case_jobs import (
    CaseJobProcessor,
    DynamoCasePreparationStore,
    _plan_from_result,
)

USER, CASE = "local-owner", "case-local"
SOURCE = "gmail:" + hashlib.sha256(b"source-id").hexdigest()
OPERATIONS = {
    "prepare_reply": ("REPLY_DRAFT", "quietpilot.reply.prepare"),
    "prepare_task": ("CHECKLIST", "quietpilot.task.prepare"),
    "prepare_reminder": ("REMINDER", "quietpilot.reminder.prepare"),
}


def context(verb: str = "prepare_reply") -> dict[str, Any]:
    return {
        "user_id": USER,
        "case_id": CASE,
        "case_type": "CONNECTED_SIGNAL",
        "goal": "선택한 메일의 후속 내용을 준비",
        "plan_version": 1,
        "risk": "LOW",
        "capability_ids": [OPERATIONS[verb][1]],
        "evidence": [
            {
                "user_id": USER,
                "ref": SOURCE,
                "revision": 3,
                "source": "gmail",
                "title": "행사 안내",
                "facts": ["received_at_unix_ms=1789344000000"],
                "untrusted_text": None,
            }
        ],
        "requested_actions": [
            {
                "connector": "quietpilot",
                "verb": verb,
                "target_resource": "case:local-content",
                "parameters": {"source_ref": SOURCE},
                "required_scopes": [],
                "risk": "LOW",
                "reversible": True,
                "verification_method": "case_plan_readback",
            }
        ],
    }


def result(source: dict[str, Any], status: str = "READY") -> dict[str, Any]:
    verb = source["requested_actions"][0]["verb"]
    marker = {
        "status": status,
        "artifact_type": OPERATIONS[verb][0] if status == "READY" else "NONE",
        "title": "행사 안내에 대한 준비" if status == "READY" else "",
        "content": "안내해 주셔서 감사합니다. 전달해 주신 자료를 참고하겠습니다."
        if status == "READY"
        else "",
        "question": "행사에 참석하시나요?" if status == "NEEDS_INPUT" else "",
        "explanation": "답장에 사용할 문안을 준비했어요."
        if status == "READY"
        else "참석 여부를 알아야 답장을 준비할 수 있어요."
        if status == "NEEDS_INPUT"
        else "자동 처리되는 안내여서 추가로 준비할 일이 없어요.",
        "source_ref": SOURCE,
    }
    actions = (
        [
            {
                **copy.deepcopy(source["requested_actions"][0]),
                "parameters": {
                    key: marker[key]
                    for key in ("source_ref", "title", "content", "artifact_type")
                },
            }
        ]
        if status == "READY"
        else []
    )
    return {
        "status": "PROPOSED",
        "committed": True,
        "external_mutation_count": 0,
        "local_preparation": marker,
        "output": {
            "case_type": source["case_type"],
            "goal": source["goal"],
            "explanation": marker["explanation"],
            "decision_question": marker["question"] or marker["explanation"],
            "evidence_revisions": {SOURCE: 3},
            "actions": actions,
        },
    }


def expected_hash(source: dict[str, Any], plan: dict[str, Any]) -> str:
    material = {
        "case_id": source["case_id"],
        "version": plan["version"],
        "evidence_revisions": {SOURCE: 3},
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
            for action in plan["actions"]
        ],
        "risk": plan["risk"],
    }
    return hashlib.sha256(
        json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


@pytest.mark.parametrize("verb", list(OPERATIONS))
def test_selected_local_artifact_is_succeeded_without_claiming_external_verification(
    verb: str,
) -> None:
    source = context(verb)
    response = result(source)
    original = copy.deepcopy(response)
    plan, summary, next_action = _plan_from_result(source, response)
    assert response == original
    assert plan["local_preparation_status"] == "READY"
    assert "local_preparation" not in plan
    action = plan["actions"][0]
    assert action["status"] == "SUCCEEDED"
    assert action["verified"] is False
    assert action["result_ref"].startswith(f"local-preparation:{CASE}:1:")
    assert action["parameters"] == response["output"]["actions"][0]["parameters"]
    assert (
        action["result_summary"]
        == summary
        == plan["reason"]
        == response["local_preparation"]["explanation"]
    )
    assert plan["hash"] == expected_hash(source, plan)
    assert plan["evidence_revisions"] == {SOURCE: 3}
    assert plan["required_scopes"] == [] and plan["available_grant_modes"] == []
    assert "No " in next_action


@pytest.mark.parametrize("status", ["NO_ACTION", "NEEDS_INPUT"])
def test_actionless_outcomes_keep_their_distinct_explanation_or_question(
    status: str,
) -> None:
    source = context()
    response = result(source, status)
    plan, summary, next_action = _plan_from_result(source, response)
    assert plan["local_preparation_status"] == status
    assert plan["actions"] == []
    assert summary == response["local_preparation"]["explanation"]
    if status == "NEEDS_INPUT":
        assert next_action == response["local_preparation"]["question"]
    else:
        assert "No further preparation" in next_action
    assert plan["hash"] == expected_hash(source, plan)


@pytest.mark.parametrize(
    "change",
    [
        "missing_marker_field",
        "marker_extra_quote",
        "uncommitted",
        "mutation",
        "boolean_zero",
        "external_action",
        "mixed_actions",
        "scopes",
        "reversible",
        "content_changed",
        "artifact_changed",
        "source_changed",
        "target_changed",
        "not_selected",
        "selected_verb_changed",
        "missing_capability",
        "other_owner",
        "boolean_revision",
        "output_revision_changed",
        "blank_content",
        "overlong_content",
        "raw_source",
        "reference_in_content",
    ],
)
def test_untrusted_or_mismatched_marker_never_completes_or_persists_an_artifact(
    change: str,
) -> None:
    source = context()
    response = result(source)
    marker, output = response["local_preparation"], response["output"]
    action = output["actions"][0]
    if change == "missing_marker_field":
        marker.pop("content")
    elif change == "marker_extra_quote":
        marker["support_quote_ref"] = "private-original-quote"
    elif change == "uncommitted":
        response["committed"] = False
    elif change == "mutation":
        response["external_mutation_count"] = 1
    elif change == "boolean_zero":
        response["external_mutation_count"] = False
    elif change == "external_action":
        action.update(connector="google", verb="calendar_event_create")
    elif change == "mixed_actions":
        output["actions"].append(copy.deepcopy(action))
    elif change == "scopes":
        action["required_scopes"] = ["calendar.events.owned"]
    elif change == "reversible":
        action["reversible"] = False
    elif change == "content_changed":
        action["parameters"]["content"] = "Different result"
    elif change == "artifact_changed":
        marker["artifact_type"] = "REMINDER"
    elif change == "source_changed":
        marker["source_ref"] = "gmail:another-owner-reference"
    elif change == "target_changed":
        action["target_resource"] = "another:target"
    elif change == "not_selected":
        source["requested_actions"] = []
    elif change == "selected_verb_changed":
        source["requested_actions"][0]["verb"] = "prepare_task"
    elif change == "missing_capability":
        source["capability_ids"] = []
    elif change == "other_owner":
        source["evidence"][0]["user_id"] = "another-owner"
    elif change == "boolean_revision":
        source["evidence"][0]["revision"] = True
    elif change == "output_revision_changed":
        output["evidence_revisions"][SOURCE] = 4
    elif change == "blank_content":
        marker["content"] = action["parameters"]["content"] = " "
    elif change == "overlong_content":
        marker["content"] = action["parameters"]["content"] = "x" * 1201
    elif change == "raw_source":
        raw = "원문 그대로 저장해서는 안 되는 비공개 메일의 긴 본문입니다. 원래 메일 내용이 여기 이어집니다."
        source["evidence"][0]["untrusted_text"] = raw
        marker["content"] = action["parameters"]["content"] = raw
    else:
        marker["content"] = action["parameters"]["content"] = SOURCE
    plan, summary, _ = _plan_from_result(source, response)
    assert "local_preparation_status" not in plan
    assert plan["actions"] == []
    assert plan["available_grant_modes"] == []
    assert "Could not verify" in summary
    assert "private-original-quote" not in json.dumps(plan)
    assert "원문 그대로 저장해서는" not in json.dumps(plan, ensure_ascii=False)


@pytest.mark.parametrize("status", ["NO_ACTION", "NEEDS_INPUT"])
def test_actionless_markers_cannot_hide_an_external_action(status: str) -> None:
    source = context()
    response = result(source, status)
    response["output"]["actions"] = result(source)["output"]["actions"]
    response["output"]["actions"][0]["connector"] = "google"
    plan, _, _ = _plan_from_result(source, response)
    assert "local_preparation_status" not in plan and plan["actions"] == []


@pytest.fixture
def database():
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
        db.create_table(
            TableName="local-cases",
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
        yield db


def seed(db: Any, source: dict[str, Any]) -> None:
    items = [
        {
            "PK": f"CASE#{CASE}",
            "SK": "META",
            "entity_type": "case",
            "user_id": USER,
            "case_id": CASE,
            "case_type": source["case_type"],
            "goal": source["goal"],
            "risk": source["risk"],
            "status": "PREPARING",
            "version": 1,
            "requested_plan_version": 1,
            "evidence_refs": [SOURCE],
            "required_capabilities": source["capability_ids"],
            "requested_actions_json": json.dumps(
                source["requested_actions"], ensure_ascii=False
            ),
            "providers": [],
            "GSI1PK": f"USER#{USER}#CASE#ACTIVE",
        },
        {
            "PK": f"CASE#{CASE}",
            "SK": "EVIDENCE#source",
            "entity_type": "case_evidence",
            "user_id": USER,
            "evidence_ref": SOURCE,
            "revision": 3,
            "source": "gmail",
            "title": source["evidence"][0]["title"],
            "facts": source["evidence"][0]["facts"],
        },
    ]
    for item in items:
        db.put_item(
            TableName="local-cases",
            Item={
                key: TypeSerializer().serialize(value) for key, value in item.items()
            },
        )


def read(db: Any, suffix: str) -> dict[str, Any]:
    item = db.get_item(
        TableName="local-cases",
        Key={"PK": {"S": f"CASE#{CASE}"}, "SK": {"S": suffix}},
        ConsistentRead=True,
    ).get("Item", {})
    return {key: TypeDeserializer().deserialize(value) for key, value in item.items()}


def process(
    db: Any,
    source: dict[str, Any],
    response: dict[str, Any],
    before_return=lambda: None,
) -> list[dict[str, object]]:
    calls = []

    def invoke(user: str, payload: dict[str, object]) -> dict[str, Any]:
        assert user == USER
        calls.append(copy.deepcopy(payload))
        before_return()
        return copy.deepcopy(response)

    processor = CaseJobProcessor(
        SimpleNamespace(invoke_payload=invoke),
        DynamoCasePreparationStore("local-cases", db),
    )
    envelope = {
        "event_type": "DIRECT_REQUEST_RECEIVED",
        "user_id": USER,
        "connector": "google",
        "payload": {"case_id": CASE, "plan_version": 1},
    }
    processor.process(envelope)
    processor.process(envelope)
    return calls


@pytest.mark.parametrize(
    ("status", "verb"),
    [("READY", verb) for verb in OPERATIONS]
    + [("NO_ACTION", "prepare_task"), ("NEEDS_INPUT", "prepare_reply")],
)
def test_real_store_persists_local_completion_and_preserves_immutable_plan(
    database: Any, status: str, verb: str
) -> None:
    source = context(verb)
    response = result(source, status)
    seed(database, source)
    original_evidence = read(database, "EVIDENCE#source")
    calls = process(database, source, response)
    assert len(calls) == 1
    meta = read(database, "META")
    completed = status in {"READY", "NO_ACTION"}
    assert meta["status"] == ("COMPLETED" if completed else "DECISION_REQUIRED")
    assert meta["GSI1PK"] == f"USER#{USER}#CASE#{'HISTORY' if completed else 'ACTIVE'}"
    assert meta["version"] == 2
    plan = json.loads(read(database, "PLAN#000001")["plan_json"])
    assert plan["hash"] == expected_hash(source, plan)
    assert plan["local_preparation_status"] == status
    assert "local_preparation" not in plan
    assert read(database, "EVIDENCE#source") == original_evidence
    if status == "READY":
        assert plan["actions"][0]["status"] == "SUCCEEDED"
        assert plan["actions"][0]["verified"] is False
        assert (
            plan["actions"][0]["parameters"]["content"]
            == response["local_preparation"]["content"]
        )
    elif status == "NEEDS_INPUT":
        assert meta["next_action"] == response["local_preparation"]["question"]
    else:
        assert plan["actions"] == []


@pytest.mark.parametrize("change", ["stop", "new_version"])
def test_late_local_preparation_cannot_complete_a_stopped_or_newer_case(
    database: Any, change: str
) -> None:
    source = context()
    seed(database, source)

    def intervene() -> None:
        database.update_item(
            TableName="local-cases",
            Key={"PK": {"S": f"CASE#{CASE}"}, "SK": {"S": "META"}},
            UpdateExpression="SET #status=:status,requested_plan_version=:version",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": {"S": "STOPPED" if change == "stop" else "PREPARING"},
                ":version": {"N": "1" if change == "stop" else "2"},
            },
        )

    calls = process(database, source, result(source), intervene)
    assert len(calls) == 1
    assert read(database, "PLAN#000001") == {}
    assert read(database, "META")["status"] == (
        "STOPPED" if change == "stop" else "PREPARING"
    )
