from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from quietpilot_control_api.workspace import (
    DynamoWorkspaceStore,
    SqsCasePreparationQueue,
    WorkspaceInputError,
    WorkspaceNotReady,
    WorkspaceService,
)
from quietpilot_worker.case_jobs import CaseJobProcessor, DynamoCasePreparationStore
from quietpilot_worker.idempotency import DynamoIdempotencyStore

from services.worker.tests.test_idempotency import FakeDynamoDb


class _Store:
    def __init__(self) -> None:
        self.case = {
            "case_id": "case-live",
            "status": "PREPARING",
            "version": 1,
        }
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_cases(self, user_id: str, bucket: str) -> list[dict[str, object]]:
        self.calls.append(("list", {"user_id": user_id, "bucket": bucket}))
        return [self.case]

    def get_case(self, user_id: str, case_id: str) -> dict[str, object] | None:
        self.calls.append(("get", {"user_id": user_id, "case_id": case_id}))
        return self.case if case_id == "case-live" else None

    def create_direct_case(self, user_id: str, **values: object):
        self.calls.append(("direct", {"user_id": user_id, **values}))
        return self.case, True

    def convert_candidates(self, user_id: str, **values: object):
        self.calls.append(("convert", {"user_id": user_id, **values}))
        return self.case, True

    def add_message(self, user_id: str, **values: object):
        self.calls.append(("message", {"user_id": user_id, **values}))
        return self.case, 2

    def transition_case(self, user_id: str, **values: object):
        self.calls.append(("transition", {"user_id": user_id, **values}))
        return {**self.case, "status": values["status"], "version": 2}


class _Queue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send(self, **values: object) -> None:
        self.calls.append(dict(values))


def _service() -> tuple[WorkspaceService, _Store, _Queue]:
    store = _Store()
    queue = _Queue()
    return WorkspaceService(store=store, queue=queue), store, queue


def test_direct_request_creates_owner_bound_preparation_job() -> None:
    service, store, queue = _service()

    result = service.create_direct_case(
        "cognito-subject",
        prompt="  이번 주 제출 준비를 정리해 줘  ",
        idempotency_key="direct-request-001",
    )

    assert result["status"] == "PREPARING"
    direct = next(values for name, values in store.calls if name == "direct")
    assert direct["user_id"] == "cognito-subject"
    assert direct["prompt"] == "이번 주 제출 준비를 정리해 줘"
    assert str(direct["evidence_ref"]).startswith("direct:")
    assert queue.calls == [
        {
            "user_id": "cognito-subject",
            "case_id": direct["case_id"],
            "connector": "direct",
            "plan_version": 1,
        }
    ]


def test_candidate_conversion_binds_versions_and_caps_selection() -> None:
    service, store, queue = _service()

    result = service.convert_candidates(
        "cognito-subject",
        candidate_ids=["candidate-1", "candidate-2"],
        expected_versions=[2, 4],
        idempotency_key="candidate-convert-001",
    )

    assert result["case_id"].startswith("case-")
    converted = next(values for name, values in store.calls if name == "convert")
    assert converted["candidate_ids"] == ["candidate-1", "candidate-2"]
    assert converted["expected_versions"] == [2, 4]
    assert queue.calls[0]["connector"] == "google"

    with pytest.raises(WorkspaceInputError):
        service.convert_candidates(
            "cognito-subject",
            candidate_ids=[f"candidate-{index}" for index in range(9)],
            expected_versions=[1] * 9,
            idempotency_key="candidate-convert-002",
        )


def test_case_chat_replans_but_approval_stays_outside_item_seven() -> None:
    service, _, queue = _service()

    result = service.post_message(
        "cognito-subject",
        case_id="case-live",
        text="시간을 오후로 바꿔 줘",
        expected_version=1,
        idempotency_key="case-message-001",
    )

    assert result["planning_job_id"] == "plan-case-live-2"
    assert queue.calls[-1]["plan_version"] == 2
    with pytest.raises(WorkspaceNotReady):
        service.decide(
            "cognito-subject",
            case_id="case-live",
            decision="APPROVE",
            expected_version=1,
        )


def test_defer_and_stop_are_distinct_live_case_transitions() -> None:
    service, store, _ = _service()

    deferred = service.decide(
        "cognito-subject",
        case_id="case-live",
        decision="DEFER",
        expected_version=1,
    )
    stopped = service.decide(
        "cognito-subject",
        case_id="case-live",
        decision="STOP",
        expected_version=1,
    )

    assert deferred["status"] == "PAUSED"
    assert stopped["status"] == "STOPPED"
    transitions = [values for name, values in store.calls if name == "transition"]
    assert [item["status"] for item in transitions] == ["PAUSED", "STOPPED"]


class _TransactionCanceled(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "TransactionCanceledException"}}


class _CaseDynamo:
    def __init__(self):
        self.items = {}

    @staticmethod
    def key(item):
        return item["PK"]["S"], item["SK"]["S"]

    def query(self, **request):
        pk = request["ExpressionAttributeValues"][":pk"]["S"]
        return {
            "Items": [
                copy.deepcopy(item)
                for key, item in sorted(self.items.items())
                if key[0] == pk
            ]
        }

    def get_item(self, *, Key, **request):
        item = self.items.get(self.key(Key))
        return {"Item": copy.deepcopy(item)} if item is not None else {}

    def transact_write_items(self, *, TransactItems):
        updated = copy.deepcopy(self.items)
        for operation in TransactItems:
            if "Put" in operation:
                put = operation["Put"]
                key = self.key(put["Item"])
                if put.get("ConditionExpression") and key in updated:
                    raise _TransactionCanceled
                updated[key] = copy.deepcopy(put["Item"])
                continue
            update = operation["Update"]
            item = updated[self.key(update["Key"])]
            values = update["ExpressionAttributeValues"]
            names = update.get("ExpressionAttributeNames", {})
            for condition in update["ConditionExpression"].split(" AND "):
                field, value = condition.split("=", 1)
                if item.get(names.get(field, field)) != values[value]:
                    raise _TransactionCanceled
            for assignment in (
                update["UpdateExpression"].removeprefix("SET ").split(",")
            ):
                field, value = [part.strip() for part in assignment.split("=", 1)]
                field = names.get(field, field)
                if "+" in value:
                    source, increment = value.split("+")
                    item[field] = {
                        "N": str(int(item[source]["N"]) + int(values[increment]["N"]))
                    }
                else:
                    item[field] = copy.deepcopy(values[value])
        self.items = updated


def test_message_during_preparation_supersedes_old_plan_and_survives_queue_deduplication():
    client = _CaseDynamo()
    messages = []
    queue = SqsCasePreparationQueue(
        "queue",
        SimpleNamespace(
            send_message=lambda **values: messages.append(
                json.loads(values["MessageBody"])
            )
        ),
    )
    service = WorkspaceService(DynamoWorkspaceStore("table", client), queue)
    created = service.create_direct_case(
        "owner", prompt="제출 준비를 정리해 줘", idempotency_key="create-case-for-race"
    )
    case_id = created["case_id"]
    calls = []

    def invoke(user_id, invocation):
        calls.append(invocation)
        if len(calls) == 1:
            service.post_message(
                user_id,
                case_id=case_id,
                text="오후에 제출할 자료를 추가해 줘",
                expected_version=1,
                idempotency_key="message-during-plan",
            )
        request = invocation["request"]
        return {
            "status": "PROPOSED",
            "committed": True,
            "external_mutation_count": 0,
            "output": {
                "case_type": request["case_type"],
                "goal": request["goal"],
                "explanation": "요청한 준비를 정리했어요.",
                "evidence_revisions": {
                    item["ref"]: item["revision"] for item in invocation["evidence"]
                },
                "preparation_steps": [],
                "actions": [],
                "decision_question": "이 범위로 준비할까요?",
            },
        }

    worker = CaseJobProcessor(
        SimpleNamespace(invoke_payload=invoke),
        DynamoCasePreparationStore("table", client),
    )
    gate = DynamoIdempotencyStore("gate", FakeDynamoDb(), in_progress_ttl_seconds=30)
    assert gate.run(messages[0], worker.process)
    pending = service.get_case("owner", case_id)
    assert pending["status"] == "PREPARING"
    assert pending["requested_plan_version"] == 2
    assert pending["current_plan_version"] is None
    assert messages[1]["dedupe_key"] != messages[0]["dedupe_key"]

    assert gate.run(messages[1], worker.process)
    completed = service.get_case("owner", case_id)
    assert completed["status"] == "DECISION_REQUIRED"
    assert completed["current_plan_version"] == 2
    assert len(calls) == 2 and len(calls[1]["evidence"]) == 2
    assert completed["plan"]["actions"] == []


def test_consecutive_pending_messages_receive_distinct_plan_generations():
    client = _CaseDynamo()
    queue = _Queue()
    service = WorkspaceService(DynamoWorkspaceStore("table", client), queue)
    case_id = service.create_direct_case(
        "owner", prompt="제출 준비", idempotency_key="create-pending-case"
    )["case_id"]
    for version in (1, 2):
        result = service.post_message(
            "owner",
            case_id=case_id,
            text=f"준비 자료 {version}개 추가",
            expected_version=version,
            idempotency_key=f"pending-message-{version}",
        )
        assert result["case"]["requested_plan_version"] == version + 1
    assert [item["plan_version"] for item in queue.calls] == [1, 2, 3]


@pytest.mark.parametrize("kind", ["direct", "conversion"])
def test_creation_retry_dispatches_the_current_pending_plan_generation(kind):
    service, store, queue = _service()
    store.case["requested_plan_version"] = 3
    if kind == "direct":
        service.create_direct_case(
            "owner", prompt="제출 준비", idempotency_key="retry-current-plan"
        )
    else:
        service.convert_candidates(
            "owner",
            candidate_ids=["candidate-one"],
            expected_versions=[1],
            idempotency_key="retry-current-plan",
        )
    assert queue.calls[0]["plan_version"] == 3
    assert queue.calls[0]["connector"] == "direct"
