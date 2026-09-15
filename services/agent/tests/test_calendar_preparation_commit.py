"""Commit only a freshly prepared Calendar plan; never claim external execution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from quietpilot_agent import agentcore_runtime
from quietpilot_agent.agentcore_runtime import (
    _calendar_case_request,
    _commit_calendar_preparation,
    run_agentcore_invocation,
)
from quietpilot_agent.calendar_planner import CalendarDraftOutputError
from quietpilot_agent.context import (
    ContextAccessDenied,
    InMemoryContextRepository,
    ProposalTransaction,
)
from quietpilot_agent.google_connector import CALENDAR_EVENTS_SCOPE
from quietpilot_agent.models import (
    ActionProposal,
    CapabilityRecord,
    CapabilityStatus,
    CaseType,
    EvidenceRecord,
    OrchestrationRequest,
    OrchestrationResult,
    OrchestrationStatus,
    ProposalStage,
    Risk,
)

from services.agent.tests.test_calendar_planner import Factory, abstain, proposal

OWNER = "synthetic-calendar-owner"
SOURCE = "direct:calendar-source"
CAPABILITY = "google.calendar.events.create"
START = "2030-01-05T10:00:00+09:00"
END = "2030-01-05T11:00:00+09:00"
BODY = f"신청하신 연구 미팅의 예약이 확정되었습니다. 시작 {START}, 종료 {END}입니다."


class NoModels:
    def create(self, *args, **kwargs):
        raise AssertionError("Unexpected model call")


@pytest.fixture
def context():
    source = EvidenceRecord(
        user_id=OWNER,
        ref=SOURCE,
        revision=7,
        source="direct",
        title="연구 미팅 안내",
        facts=["source_content=body"],
        untrusted_text=BODY,
    )
    capability = CapabilityRecord(
        user_id=OWNER,
        capability_id=CAPABILITY,
        connector="google",
        status=CapabilityStatus.AVAILABLE,
        operations=["google.calendar_event_create"],
        required_scopes=[CALENDAR_EVENTS_SCOPE],
    )
    action = ActionProposal(
        connector="google",
        target_resource="primary",
        verb="calendar_event_create",
        parameters={
            "summary": "Research meeting",
            "start": START,
            "end": END,
            "source_ref": SOURCE,
        },
        required_scopes=[CALENDAR_EVENTS_SCOPE],
        risk=Risk.MEDIUM,
        reversible=True,
        verification_method="calendar_event_readback",
    )
    request = OrchestrationRequest(
        user_id=OWNER,
        case_type=CaseType.DIRECT_DELEGATION,
        proposal_stage=ProposalStage.CASE_PLANNING,
        goal="연구 미팅 일정을 준비해 줘.",
        evidence_refs=[SOURCE],
        capability_ids=[CAPABILITY],
        primary_group_hint="일정",
        risk=Risk.MEDIUM,
        requested_actions=[action],
    )
    repository = InMemoryContextRepository(evidence=[source], capabilities=[capability])
    return SimpleNamespace(
        source=source,
        capability=capability,
        action=action,
        request=request,
        repository=repository,
    )


def invocation(context, request=None):
    return {
        "request": (request or context.request).model_dump(mode="json"),
        "evidence": [context.source.model_dump(mode="json")],
        "capabilities": [context.capability.model_dump(mode="json")],
    }


@pytest.mark.parametrize(
    "request_risk,action_risk",
    [(Risk.MEDIUM, Risk.MEDIUM), (Risk.MEDIUM, Risk.HIGH), (Risk.HIGH, Risk.HIGH)],
)
def test_valid_commit_stages_exact_action_and_current_revisions_without_execution_history(
    context, monkeypatch, request_risk, action_risk
):
    action = context.action.model_copy(update={"risk": action_risk})
    request = context.request.model_copy(
        update={"risk": request_risk, "requested_actions": [action]}
    )
    original = request.model_dump(mode="json")
    calls = []
    stage = ProposalTransaction.stage_case_plan
    commit = ProposalTransaction.commit

    def observe_stage(self, payload):
        calls.append("stage")
        return stage(self, payload)

    def observe_commit(self, output):
        calls.append("commit")
        assert self.staged == output
        return commit(self, output)

    monkeypatch.setattr(ProposalTransaction, "stage_case_plan", observe_stage)
    monkeypatch.setattr(ProposalTransaction, "commit", observe_commit)
    result = _commit_calendar_preparation(request, context.repository)
    assert calls == ["stage", "commit"]
    assert result["status"] == "PROPOSED" and result["committed"] is True
    assert result["external_mutation_count"] == result["output_attempts"] == 0
    assert result["tool_calls"] == [] and result["tool_registry"] == {}
    output = result["output"]
    assert output["actions"] == [action.model_dump(mode="json")]
    assert output["evidence_revisions"] == {SOURCE: 7}
    assert (
        output["goal"] == request.goal
        and output["case_type"] == request.case_type.value
    )
    assert request.model_dump(mode="json") == original
    assert BODY not in str(result)
    assert "local_preparation" not in result


@pytest.mark.parametrize(
    "change",
    [
        {"connector": "other"},
        {"verb": "calendar_event_delete"},
        {"target_resource": "another-calendar"},
        {"required_scopes": []},
        {"required_scopes": [CALENDAR_EVENTS_SCOPE, "unexpected"]},
        {"required_scopes": [CALENDAR_EVENTS_SCOPE, CALENDAR_EVENTS_SCOPE]},
        {"reversible": False},
        {"verification_method": "skip_readback"},
        {"risk": Risk.LOW},
    ],
)
def test_changed_calendar_action_boundary_is_rejected(context, change):
    action = ActionProposal.model_validate(
        {**context.action.model_dump(mode="json"), **change}
    )
    request = context.request.model_copy(update={"requested_actions": [action]})
    with pytest.raises((ContextAccessDenied, ValueError)):
        _commit_calendar_preparation(request, context.repository)


@pytest.mark.parametrize(
    "boundary",
    [
        "missing_source",
        "foreign_source",
        "unknown_evidence",
        "foreign_owner",
        "risk_floor",
        "conflict",
        "discovery",
        "no_action",
        "multiple_actions",
    ],
)
def test_selected_case_source_and_request_contract_cannot_change(context, boundary):
    request, repository = context.request, context.repository
    if boundary in {"missing_source", "foreign_source"}:
        parameters = dict(context.action.parameters)
        if boundary == "missing_source":
            parameters.pop("source_ref")
        else:
            parameters["source_ref"] = "direct:other-source"
        request = request.model_copy(
            update={
                "requested_actions": [
                    context.action.model_copy(update={"parameters": parameters})
                ]
            }
        )
    elif boundary == "unknown_evidence":
        request = request.model_copy(update={"evidence_refs": ["direct:missing"]})
    elif boundary == "foreign_owner":
        repository = InMemoryContextRepository(
            evidence=[context.source.model_copy(update={"user_id": "other-owner"})],
            capabilities=[context.capability],
        )
    elif boundary == "risk_floor":
        request = request.model_copy(update={"risk": Risk.HIGH})
    elif boundary == "conflict":
        request = request.model_copy(update={"conflicting_evidence": True})
    elif boundary == "discovery":
        request = request.model_copy(update={"proposal_stage": ProposalStage.DISCOVERY})
    elif boundary == "no_action":
        request = request.model_copy(update={"requested_actions": []})
    else:
        request = request.model_copy(
            update={"requested_actions": [context.action, context.action]}
        )
    with pytest.raises((ContextAccessDenied, ValueError)):
        _commit_calendar_preparation(request, repository)


@pytest.mark.parametrize(
    "change",
    [
        {"status": CapabilityStatus.INACCESSIBLE},
        {"status": CapabilityStatus.MISSING},
        {"connector": "other"},
        {"operations": []},
        {"operations": ["google.calendar_event_delete"]},
        {"required_scopes": ["unexpected"]},
        {"user_id": "other-owner"},
    ],
)
def test_unavailable_or_out_of_scope_capability_cannot_commit(context, change):
    capability = context.capability.model_copy(update=change)
    repository = InMemoryContextRepository(
        evidence=[context.source], capabilities=[capability]
    )
    with pytest.raises((ContextAccessDenied, ValueError)):
        _commit_calendar_preparation(context.request, repository)


@pytest.mark.parametrize("capability_ids", [[], ["google.calendar.unselected"]])
def test_calendar_capability_must_belong_to_the_requested_scope(
    context, capability_ids
):
    with pytest.raises((ContextAccessDenied, ValueError)):
        _commit_calendar_preparation(
            context.request.model_copy(update={"capability_ids": capability_ids}),
            context.repository,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"description": ""},
        {"start": START.replace("T", "t")},
        {"start": "2030-01-05T10:00:00"},
        {"end": START},
        {"timeZone": "America/New_York"},
        {"attendees": ["synthetic@example.invalid"]},
    ],
)
def test_calendar_parameters_must_already_be_normalized_and_bounded(context, change):
    action = context.action.model_copy(
        update={"parameters": {**context.action.parameters, **change}}
    )
    with pytest.raises((ContextAccessDenied, ValueError)):
        _commit_calendar_preparation(
            context.request.model_copy(update={"requested_actions": [action]}),
            context.repository,
        )


@pytest.mark.parametrize("revision", [True, 0, -1, "7"])
def test_invalid_caller_evidence_revision_fails_before_any_preparation(
    context, revision
):
    payload = invocation(context)
    payload["evidence"][0]["revision"] = revision
    with pytest.raises(ValidationError):
        run_agentcore_invocation(payload, None, model_factory=NoModels())


def test_transaction_rejects_a_stale_revision_at_the_stage_boundary(
    context, monkeypatch
):
    stage = ProposalTransaction.stage_case_plan

    def corrupt_transfer(self, payload):
        return stage(self, {**payload, "evidence_revisions": {SOURCE: 6}})

    monkeypatch.setattr(ProposalTransaction, "stage_case_plan", corrupt_transfer)
    with pytest.raises(ContextAccessDenied):
        _commit_calendar_preparation(context.request, context.repository)


def test_two_fresh_source_assessments_are_the_only_successful_marker_producer(context):
    selected = context.request.model_copy(update={"requested_actions": []})
    payload = proposal(start=START, end=END)
    factory = Factory(payload, payload)
    prepared, freshly_prepared = _calendar_case_request(
        selected, context.repository, factory
    )
    assert freshly_prepared is True
    assert prepared.requested_actions == [context.action]
    assert list(factory.models) == ["calendar_draft_planner", "calendar_draft_verifier"]
    assert all(model.stream_calls == 1 for model in factory.models.values())


def test_verified_abstention_has_no_prepared_marker(context):
    selected = context.request.model_copy(update={"requested_actions": []})
    payload = proposal(start=START, end=END)
    prepared, freshly_prepared = _calendar_case_request(
        selected, context.repository, Factory(payload, abstain())
    )
    assert freshly_prepared is False and prepared.requested_actions == []


def test_failed_fresh_verifier_does_not_release_the_accepted_calendar_draft(context):
    selected = context.request.model_copy(update={"requested_actions": []})
    payload = proposal(start=START, end=END)
    invalid = {**payload, "end": "2030-01-05T12:00:00+09:00"}
    with pytest.raises(CalendarDraftOutputError):
        _calendar_case_request(selected, context.repository, Factory(payload, invalid))


@pytest.mark.parametrize("required_scopes", [[], ["unexpected"]])
def test_incomplete_calendar_inventory_cannot_issue_a_fresh_marker(
    context, required_scopes
):
    selected = context.request.model_copy(update={"requested_actions": []})
    repository = InMemoryContextRepository(
        evidence=[context.source],
        capabilities=[
            context.capability.model_copy(update={"required_scopes": required_scopes})
        ],
    )
    prepared, freshly_prepared = _calendar_case_request(
        selected, repository, NoModels()
    )
    assert prepared == selected and freshly_prepared is False


@pytest.mark.parametrize("nested_marker", [False, True])
def test_caller_provided_calendar_action_cannot_enter_fresh_commit_path(
    context, monkeypatch, nested_marker
):
    request = context.request
    if nested_marker:
        request = request.model_copy(
            update={
                "requested_actions": [
                    context.action.model_copy(
                        update={
                            "parameters": {
                                **context.action.parameters,
                                "calendar_prepared": True,
                            }
                        }
                    )
                ]
            }
        )
    prepared, freshly_prepared = _calendar_case_request(
        request, context.repository, NoModels()
    )
    assert prepared == request and freshly_prepared is False
    regular_requests = []

    def regular_graph(self, supplied):
        regular_requests.append(supplied)
        return OrchestrationResult(
            status=OrchestrationStatus.EXCEPTION_REQUIRED,
            output=None,
            error_code="AGENT_OUTPUT_INVALID",
            output_attempts=0,
            committed=False,
            tool_calls=[],
            tool_registry={},
        )

    def forbidden_commit(*args):
        raise AssertionError("Caller-provided action was treated as freshly prepared")

    monkeypatch.setattr(agentcore_runtime.ProposalOrchestrator, "run", regular_graph)
    monkeypatch.setattr(
        agentcore_runtime, "_commit_calendar_preparation", forbidden_commit
    )
    result = run_agentcore_invocation(
        invocation(context, request), None, model_factory=NoModels()
    )
    assert regular_requests == [request] and result["committed"] is False


@pytest.mark.parametrize("field", ["calendar_prepared", "freshly_prepared"])
@pytest.mark.parametrize("location", ["envelope", "request"])
def test_caller_cannot_supply_a_fresh_preparation_flag(context, field, location):
    payload = invocation(context)
    target = payload if location == "envelope" else payload["request"]
    target[field] = True
    with pytest.raises(ValidationError):
        run_agentcore_invocation(payload, None, model_factory=NoModels())
