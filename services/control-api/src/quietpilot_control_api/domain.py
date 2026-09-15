"""Deterministic QuietPilot aggregate state transitions."""

from enum import StrEnum


class InvalidTransition(ValueError):
    """Raised when an aggregate requests a forbidden state transition."""


class CandidateStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    GROUPED = "GROUPED"
    VISIBLE = "VISIBLE"
    SELECTED = "SELECTED"
    CONVERTED = "CONVERTED"
    HIDDEN = "HIDDEN"
    EXPIRED = "EXPIRED"


class CaseStatus(StrEnum):
    PREPARING = "PREPARING"
    DECISION_REQUIRED = "DECISION_REQUIRED"
    APPROVED = "APPROVED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    PERMISSION_REVOKED = "PERMISSION_REVOKED"
    STOPPED = "STOPPED"


class RoutineStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REVOKED = "REVOKED"


class ActionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    DEFER = "DEFER"
    STOP = "STOP"


CANDIDATE_TRANSITIONS: dict[CandidateStatus, frozenset[CandidateStatus]] = {
    CandidateStatus.DISCOVERED: frozenset({CandidateStatus.GROUPED}),
    CandidateStatus.GROUPED: frozenset({CandidateStatus.VISIBLE}),
    CandidateStatus.VISIBLE: frozenset(
        {
            CandidateStatus.SELECTED,
            CandidateStatus.HIDDEN,
            CandidateStatus.EXPIRED,
        }
    ),
    CandidateStatus.SELECTED: frozenset(
        {CandidateStatus.CONVERTED, CandidateStatus.VISIBLE}
    ),
    CandidateStatus.CONVERTED: frozenset(),
    CandidateStatus.HIDDEN: frozenset(),
    CandidateStatus.EXPIRED: frozenset(),
}


CASE_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.PREPARING: frozenset(
        {
            CaseStatus.DECISION_REQUIRED,
            CaseStatus.APPROVED,
            CaseStatus.PERMISSION_REVOKED,
            CaseStatus.STOPPED,
        }
    ),
    CaseStatus.DECISION_REQUIRED: frozenset(
        {
            CaseStatus.APPROVED,
            CaseStatus.PAUSED,
            CaseStatus.PERMISSION_REVOKED,
            CaseStatus.STOPPED,
        }
    ),
    CaseStatus.APPROVED: frozenset(
        {CaseStatus.QUEUED, CaseStatus.PERMISSION_REVOKED, CaseStatus.STOPPED}
    ),
    CaseStatus.QUEUED: frozenset(
        {
            CaseStatus.RUNNING,
            CaseStatus.FAILED,
            CaseStatus.PERMISSION_REVOKED,
            CaseStatus.STOPPED,
        }
    ),
    CaseStatus.RUNNING: frozenset(
        {
            CaseStatus.VERIFYING,
            CaseStatus.DECISION_REQUIRED,
            CaseStatus.FAILED,
            CaseStatus.PAUSED,
            CaseStatus.PERMISSION_REVOKED,
        }
    ),
    CaseStatus.VERIFYING: frozenset(
        {
            CaseStatus.COMPLETED,
            CaseStatus.DECISION_REQUIRED,
            CaseStatus.FAILED,
            CaseStatus.PERMISSION_REVOKED,
        }
    ),
    CaseStatus.FAILED: frozenset(
        {CaseStatus.QUEUED, CaseStatus.PERMISSION_REVOKED, CaseStatus.STOPPED}
    ),
    CaseStatus.PAUSED: frozenset(
        {
            CaseStatus.DECISION_REQUIRED,
            CaseStatus.QUEUED,
            CaseStatus.PERMISSION_REVOKED,
            CaseStatus.STOPPED,
        }
    ),
    CaseStatus.PERMISSION_REVOKED: frozenset(
        {CaseStatus.DECISION_REQUIRED, CaseStatus.STOPPED}
    ),
    CaseStatus.COMPLETED: frozenset(),
    CaseStatus.STOPPED: frozenset(),
}


ROUTINE_TRANSITIONS: dict[RoutineStatus, frozenset[RoutineStatus]] = {
    RoutineStatus.PROPOSED: frozenset({RoutineStatus.ACTIVE, RoutineStatus.REVOKED}),
    RoutineStatus.ACTIVE: frozenset({RoutineStatus.PAUSED, RoutineStatus.REVOKED}),
    RoutineStatus.PAUSED: frozenset({RoutineStatus.ACTIVE, RoutineStatus.REVOKED}),
    RoutineStatus.REVOKED: frozenset(),
}


ACTION_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.PROPOSED: frozenset({ActionStatus.APPROVED, ActionStatus.BLOCKED}),
    ActionStatus.APPROVED: frozenset({ActionStatus.QUEUED, ActionStatus.CANCELLED}),
    ActionStatus.QUEUED: frozenset({ActionStatus.RUNNING}),
    ActionStatus.RUNNING: frozenset({ActionStatus.VERIFYING, ActionStatus.FAILED}),
    ActionStatus.VERIFYING: frozenset({ActionStatus.SUCCEEDED, ActionStatus.FAILED}),
    ActionStatus.SUCCEEDED: frozenset(),
    ActionStatus.BLOCKED: frozenset(),
    ActionStatus.CANCELLED: frozenset(),
    ActionStatus.FAILED: frozenset(),
}


def transition_candidate(
    current: CandidateStatus, target: CandidateStatus
) -> CandidateStatus:
    if target not in CANDIDATE_TRANSITIONS[current]:
        raise InvalidTransition(f"Candidate cannot transition {current} -> {target}")
    return target


def transition_case(current: CaseStatus, target: CaseStatus) -> CaseStatus:
    if target not in CASE_TRANSITIONS[current]:
        raise InvalidTransition(f"Case cannot transition {current} -> {target}")
    return target


def transition_routine(current: RoutineStatus, target: RoutineStatus) -> RoutineStatus:
    if target not in ROUTINE_TRANSITIONS[current]:
        raise InvalidTransition(f"Routine cannot transition {current} -> {target}")
    return target


def transition_action(current: ActionStatus, target: ActionStatus) -> ActionStatus:
    if target not in ACTION_TRANSITIONS[current]:
        raise InvalidTransition(f"Action cannot transition {current} -> {target}")
    return target


def record_approval_decision(
    current: ApprovalDecision | None, target: ApprovalDecision
) -> ApprovalDecision:
    if current is not None:
        raise InvalidTransition(
            f"Approval decision is immutable once recorded: {current}"
        )
    return target
