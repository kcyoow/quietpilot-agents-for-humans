import pytest
from quietpilot_control_api.domain import (
    ActionStatus,
    ApprovalDecision,
    CandidateStatus,
    CaseStatus,
    InvalidTransition,
    RoutineStatus,
    record_approval_decision,
    transition_action,
    transition_candidate,
    transition_case,
    transition_routine,
)


def test_candidate_happy_path() -> None:
    status = CandidateStatus.DISCOVERED
    for target in (
        CandidateStatus.GROUPED,
        CandidateStatus.VISIBLE,
        CandidateStatus.SELECTED,
        CandidateStatus.CONVERTED,
    ):
        status = transition_candidate(status, target)
    assert status is CandidateStatus.CONVERTED


def test_completed_case_is_terminal() -> None:
    with pytest.raises(InvalidTransition):
        transition_case(CaseStatus.COMPLETED, CaseStatus.RUNNING)


def test_material_change_can_return_to_decision() -> None:
    assert (
        transition_case(CaseStatus.RUNNING, CaseStatus.DECISION_REQUIRED)
        is CaseStatus.DECISION_REQUIRED
    )


def test_permission_revocation_interrupts_nonterminal_case() -> None:
    assert (
        transition_case(CaseStatus.QUEUED, CaseStatus.PERMISSION_REVOKED)
        is CaseStatus.PERMISSION_REVOKED
    )


def test_routine_must_be_activated_before_running() -> None:
    with pytest.raises(InvalidTransition):
        transition_routine(RoutineStatus.PROPOSED, RoutineStatus.PAUSED)
    assert (
        transition_routine(RoutineStatus.PROPOSED, RoutineStatus.ACTIVE)
        is RoutineStatus.ACTIVE
    )


def test_action_acceptance_is_not_external_success() -> None:
    status = ActionStatus.PROPOSED
    for target in (
        ActionStatus.APPROVED,
        ActionStatus.QUEUED,
        ActionStatus.RUNNING,
        ActionStatus.VERIFYING,
        ActionStatus.SUCCEEDED,
    ):
        status = transition_action(status, target)
    assert status is ActionStatus.SUCCEEDED


def test_action_cannot_skip_verification() -> None:
    with pytest.raises(InvalidTransition):
        transition_action(ActionStatus.RUNNING, ActionStatus.SUCCEEDED)


def test_approval_decision_is_immutable() -> None:
    decision = record_approval_decision(None, ApprovalDecision.APPROVE)
    with pytest.raises(InvalidTransition):
        record_approval_decision(decision, ApprovalDecision.STOP)
