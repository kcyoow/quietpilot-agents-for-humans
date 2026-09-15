from __future__ import annotations

from types import SimpleNamespace

import pytest
from quietpilot_worker.case_jobs import CaseJobProcessor


def _context() -> dict[str, object]:
    return {
        "case_id": "case-live",
        "case_type": "DIRECT_DELEGATION",
        "goal": "이번 주 제출 준비를 정리해 줘",
        "plan_version": 1,
        "risk": "LOW",
        "evidence": [
            {
                "user_id": "cognito-subject",
                "ref": "direct:one",
                "revision": 1,
                "source": "direct",
                "title": "제출 준비",
                "facts": ["사용자가 직접 맡긴 요청"],
                "untrusted_text": "이번 주 제출 준비를 정리해 줘",
            }
        ],
    }


class _Runtime:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    def invoke_payload(self, user_id: str, payload):
        self.calls.append((user_id, dict(payload)))
        return self.result


class _Store:
    def __init__(self, context: dict[str, object] | None = None) -> None:
        self.context = context
        self.writes: list[dict[str, object]] = []

    def load(self, user_id: str, case_id: str, plan_version: int):
        assert user_id == "cognito-subject"
        assert case_id == "case-live"
        assert plan_version == 1
        return self.context

    def write_plan(
        self,
        user_id: str,
        case_id: str,
        plan_version: int,
        **values: object,
    ) -> None:
        self.writes.append(
            {
                "user_id": user_id,
                "case_id": case_id,
                "plan_version": plan_version,
                **values,
            }
        )


def _envelope() -> dict[str, object]:
    return {
        "event_type": "DIRECT_REQUEST_RECEIVED",
        "user_id": "cognito-subject",
        "connector": "direct",
        "payload": {"case_id": "case-live", "plan_version": 1},
    }


def test_case_job_invokes_proposal_only_runtime_and_persists_plan() -> None:
    result = {
        "status": "PROPOSED",
        "committed": True,
        "external_mutation_count": 0,
        "output": {
            "case_type": "DIRECT_DELEGATION",
            "goal": "이번 주 제출 준비를 정리해 줘",
            "explanation": "요청을 제출 준비 Case로 정리했어요.",
            "evidence_revisions": {"direct:one": 1},
            "preparation_steps": ["필요한 자료 확인"],
            "actions": [],
            "decision_question": "이 범위로 준비를 이어갈까요?",
        },
    }
    runtime = _Runtime(result)
    store = _Store(_context())

    CaseJobProcessor(runtime, store).process(_envelope())

    assert runtime.calls[0][0] == "cognito-subject"
    request = runtime.calls[0][1]["request"]
    assert request["user_id"] == "cognito-subject"
    assert request["proposal_stage"] == "CASE_PLANNING"
    assert request["requested_actions"] == []
    assert store.writes[0]["summary"] == "요청을 제출 준비 Case로 정리했어요."
    plan = store.writes[0]["plan"]
    assert plan["actions"] == []
    assert len(plan["hash"]) == 64


def test_invalid_agent_output_becomes_actionless_review_plan() -> None:
    runtime = _Runtime(
        {
            "status": "EXCEPTION_REQUIRED",
            "committed": False,
            "external_mutation_count": 0,
            "output": {},
        }
    )
    store = _Store(_context())

    CaseJobProcessor(runtime, store).process(_envelope())

    plan = store.writes[0]["plan"]
    assert plan["actions"] == []
    assert plan["required_scopes"] == []
    assert "No external action" in store.writes[0]["summary"]


def test_action_ready_candidate_keeps_its_grounded_action_in_case_planning() -> None:
    context = _context()
    context.update(
        {
            "case_type": "CONNECTED_SIGNAL",
            "goal": "마감 전에 끝낼 일을 준비",
            "capability_ids": ["quietpilot.task.prepare"],
            "requested_actions": [
                {
                    "connector": "quietpilot",
                    "target_resource": "case:deadline",
                    "verb": "prepare_task",
                    "parameters": {"source_ref": "direct:one"},
                    "required_scopes": [],
                    "risk": "LOW",
                    "reversible": True,
                    "verification_method": "case_plan_readback",
                }
            ],
        }
    )
    result = {
        "status": "PROPOSED",
        "committed": True,
        "external_mutation_count": 0,
        "output": {
            "case_type": "CONNECTED_SIGNAL",
            "goal": "마감 전에 끝낼 일을 준비",
            "explanation": "할 일과 체크 항목을 준비했어요.",
            "evidence_revisions": {"direct:one": 1},
            "preparation_steps": [],
            "actions": context["requested_actions"],
            "decision_question": "이대로 준비할까요?",
        },
    }
    runtime = _Runtime(result)
    store = _Store(context)

    CaseJobProcessor(runtime, store).process(_envelope())

    invocation = runtime.calls[0][1]
    assert invocation["request"]["capability_ids"] == [  # type: ignore[index]
        "quietpilot.task.prepare"
    ]
    assert invocation["request"]["requested_actions"][0]["verb"] == (  # type: ignore[index]
        "prepare_task"
    )
    assert store.writes[0]["plan"]["actions"][0]["label"] == (  # type: ignore[index]
        "Prepare checklist"
    )


def test_stale_or_stopped_case_does_not_invoke_agentcore() -> None:
    runtime = _Runtime({})
    store = _Store(None)

    CaseJobProcessor(runtime, store).process(_envelope())

    assert runtime.calls == []
    assert store.writes == []


@pytest.mark.parametrize("error_type", [TimeoutError, RuntimeError])
def test_runtime_failure_becomes_a_recoverable_empty_plan_without_private_logs(
    error_type, caplog
):
    store = _Store(_context())

    def fail(*args):
        raise error_type("private body and provider token")

    CaseJobProcessor(SimpleNamespace(invoke_payload=fail), store).process(_envelope())
    assert len(store.writes) == 1
    assert store.writes[0]["plan"]["actions"] == []
    assert store.writes[0]["plan"]["available_grant_modes"] == []
    assert "Could not verify" in store.writes[0]["summary"]
    assert "private body" not in caplog.text and "provider token" not in caplog.text
