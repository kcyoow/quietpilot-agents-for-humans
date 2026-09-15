from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from boto3.dynamodb.types import TypeSerializer
from quietpilot_worker.case_jobs import (
    CaseJobProcessor,
    DynamoCasePreparationStore,
    _agent_invocation,
    _plan_from_result,
)

from .test_local_preparation_jobs import CASE, SOURCE, USER, context, read, result, seed
from .test_local_preparation_jobs import database as database_fixture

ORIGINAL = "- 첫 번째 항목 준비\n- 두 번째 항목 준비\n- 세 번째 항목 준비"
REVISED = "- 첫 번째 항목 준비\n- 세 번째 항목 준비"


@pytest.fixture
def db():
    environment = database_fixture.__wrapped__()
    try:
        yield next(environment)
    finally:
        environment.close()


def put(db: Any, suffix: str, values: dict[str, Any]) -> None:
    item = {**values, "PK": f"CASE#{CASE}", "SK": suffix}
    db.put_item(
        TableName="local-cases",
        Item={key: TypeSerializer().serialize(value) for key, value in item.items()},
    )


def next_generation(db: Any, generation: int) -> None:
    meta = read(db, "META")
    message_ref = f"message:edit-{generation}"
    put(
        db,
        "META",
        {
            **meta,
            "status": "PREPARING",
            "requested_plan_version": generation,
            "version": int(meta["version"]) + 1,
            "evidence_refs": [*meta["evidence_refs"], message_ref],
        },
    )
    put(
        db,
        f"EVIDENCE#edit-{generation}",
        {
            "entity_type": "case_evidence",
            "user_id": USER,
            "evidence_ref": message_ref,
            "revision": 1,
            "source": "direct",
            "title": "보완 요청",
            "facts": [],
            "untrusted_text": "두 번째 항목은 빼줘",
        },
    )


def completed(
    db: Any, status: str = "READY"
) -> tuple[DynamoCasePreparationStore, dict[str, Any]]:
    source = context("prepare_task")
    seed(db, source)
    response = result(source, status)
    if status == "READY":
        response["local_preparation"]["content"] = ORIGINAL
        response["output"]["actions"][0]["parameters"]["content"] = ORIGINAL
    plan, summary, question = _plan_from_result(source, response)
    store = DynamoCasePreparationStore("local-cases", db)
    store.write_plan(
        USER, CASE, 1, plan=plan, summary=summary, decision_question=question
    )
    next_generation(db, 2)
    return store, source


def envelope(generation: int = 2, user: str = USER) -> dict[str, object]:
    return {
        "event_type": "DIRECT_REQUEST_RECEIVED",
        "connector": "google",
        "user_id": user,
        "payload": {"case_id": CASE, "plan_version": generation},
    }


def test_previous_validated_checklist_reaches_runtime_separately_and_is_not_copied_to_new_plan(
    db: Any,
) -> None:
    store, source = completed(db)
    old_plan = read(db, "PLAN#000001")
    calls = []

    def invoke(user: str, invocation: dict[str, Any]) -> dict[str, Any]:
        assert user == USER
        calls.append(copy.deepcopy(invocation))
        response = result({**source, "plan_version": 2})
        response["output"]["evidence_revisions"] = {
            item["ref"]: item["revision"] for item in invocation["evidence"]
        }
        response["local_preparation"]["content"] = REVISED
        response["output"]["actions"][0]["parameters"]["content"] = REVISED
        return response

    processor = CaseJobProcessor(SimpleNamespace(invoke_payload=invoke), store)
    processor.process(envelope())
    processor.process(envelope())
    assert len(calls) == 1
    invocation = calls[0]
    previous = invocation["previous_local_preparation"]
    assert previous == {
        "status": "READY",
        "artifact_type": "CHECKLIST",
        "title": "행사 안내에 대한 준비",
        "content": ORIGINAL,
        "question": "",
        "explanation": "답장에 사용할 문안을 준비했어요.",
        "source_ref": SOURCE,
    }
    assert ORIGINAL not in json.dumps(invocation["evidence"], ensure_ascii=False)
    assert all(
        "content" not in action["parameters"]
        for action in invocation["request"]["requested_actions"]
    )
    assert any(
        item["source"] == "direct" and item["untrusted_text"] == "두 번째 항목은 빼줘"
        for item in invocation["evidence"]
    )
    assert read(db, "PLAN#000001") == old_plan
    new_plan = json.loads(read(db, "PLAN#000002")["plan_json"])
    assert new_plan["actions"][0]["parameters"]["content"] == REVISED
    assert "previous_local_preparation" not in new_plan
    assert ORIGINAL not in read(db, "PLAN#000002")["plan_json"]
    next_generation(db, 3)
    latest = store.load(USER, CASE, 3)
    assert latest["previous_local_preparation"]["content"] == REVISED


def test_previous_no_action_has_only_the_seven_bounded_editing_fields(db: Any) -> None:
    store, _ = completed(db, "NO_ACTION")
    value = store.load(USER, CASE, 2)
    previous = value["previous_local_preparation"]
    assert previous == {
        "status": "NO_ACTION",
        "artifact_type": "NONE",
        "title": "",
        "content": "",
        "question": "",
        "explanation": "자동 처리되는 안내여서 추가로 준비할 일이 없어요.",
        "source_ref": SOURCE,
    }
    assert _agent_invocation(USER, value)["previous_local_preparation"] == previous


def test_first_preparation_and_calendar_requests_do_not_receive_a_previous_local_artifact(
    db: Any,
) -> None:
    source = context()
    seed(db, source)
    store = DynamoCasePreparationStore("local-cases", db)
    first = store.load(USER, CASE, 1)
    assert "previous_local_preparation" not in first
    assert "previous_local_preparation" not in _agent_invocation(USER, first)
    store, source = completed(db)
    meta = read(db, "META")
    selected = source["requested_actions"]
    selected[0]["connector"] = "google"
    selected[0]["verb"] = "calendar_event_create"
    put(db, "META", {**meta, "requested_actions_json": json.dumps(selected)})
    assert "previous_local_preparation" not in store.load(USER, CASE, 2)


def test_previous_needs_input_question_is_not_reclassified_as_an_ai_artifact(
    db: Any,
) -> None:
    store, _ = completed(db, "NEEDS_INPUT")
    assert "previous_local_preparation" not in store.load(USER, CASE, 2)


@pytest.mark.parametrize("boundary", ["owner", "stale", "duplicate"])
def test_wrong_owner_and_stale_or_completed_generation_are_ignored(
    db: Any, boundary: str
) -> None:
    store, _ = completed(db)
    if boundary == "duplicate":
        put(db, "META", {**read(db, "META"), "current_plan_version": 2})
    value = store.load(
        "another-owner" if boundary == "owner" else USER,
        CASE,
        1 if boundary == "stale" else 2,
    )
    assert value is None


def write_changed_plan(db: Any, plan: dict[str, Any], *, update_hash: bool) -> None:
    if update_hash:
        material = {
            "case_id": CASE,
            "version": plan["version"],
            "evidence_revisions": plan["evidence_revisions"],
            "risk": plan["risk"],
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
        }
        plan["hash"] = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        put(db, "META", {**read(db, "META"), "current_plan_hash": plan["hash"]})
    put(
        db,
        "PLAN#000001",
        {
            **read(db, "PLAN#000001"),
            "plan_hash": plan["hash"],
            "plan_json": json.dumps(plan, ensure_ascii=False),
        },
    )


@pytest.mark.parametrize(
    "change",
    [
        "material",
        "hash",
        "source",
        "source_owner",
        "revision",
        "external_action",
        "external_approval",
        "external_execution",
        "verified",
        "overlong",
    ],
)
def test_corrupted_previous_artifact_stops_runtime_and_finishes_as_a_safe_decision(
    db: Any, change: str
) -> None:
    store, _ = completed(db)
    plan = json.loads(read(db, "PLAN#000001")["plan_json"])
    if change == "material":
        plan["actions"][0]["parameters"]["content"] = (
            "CORRUPT_AI_CONTENT_SHOULD_NOT_LEAVE_STORE"
        )
        write_changed_plan(db, plan, update_hash=False)
    elif change == "hash":
        put(db, "META", {**read(db, "META"), "current_plan_hash": "0" * 64})
    elif change == "source":
        plan["actions"][0]["parameters"]["source_ref"] = "gmail:another-source"
        write_changed_plan(db, plan, update_hash=True)
    elif change == "source_owner":
        put(
            db,
            "EVIDENCE#source",
            {**read(db, "EVIDENCE#source"), "user_id": "another-owner"},
        )
    elif change == "revision":
        put(db, "EVIDENCE#source", {**read(db, "EVIDENCE#source"), "revision": 4})
    elif change == "external_action":
        plan["actions"][0]["connector"] = "google"
        write_changed_plan(db, plan, update_hash=True)
    elif change in {"external_approval", "external_execution"}:
        put(
            db,
            "FOREIGN#external",
            {
                "entity_type": "approval"
                if change == "external_approval"
                else "action_execution",
                "user_id": USER,
            },
        )
    elif change == "verified":
        plan["actions"][0]["verified"] = True
        write_changed_plan(db, plan, update_hash=False)
    else:
        plan["actions"][0]["parameters"]["content"] = "x" * 1201
        write_changed_plan(db, plan, update_hash=True)
    prior_row = read(db, "PLAN#000001")
    loaded = store.load(USER, CASE, 2)
    assert loaded["preparation_error"] == "CASE_PREPARATION_FAILED"
    assert "previous_local_preparation" not in loaded
    calls = []
    processor = CaseJobProcessor(
        SimpleNamespace(invoke_payload=lambda *args: calls.append(args)), store
    )
    processor.process(envelope())
    processor.process(envelope())
    assert calls == []
    assert read(db, "META")["status"] == "DECISION_REQUIRED"
    fallback = json.loads(read(db, "PLAN#000002")["plan_json"])
    assert fallback["actions"] == []
    assert "previous_local_preparation" not in fallback
    assert "CORRUPT_AI_CONTENT" not in read(db, "PLAN#000002")["plan_json"]
    assert read(db, "PLAN#000001") == prior_row
