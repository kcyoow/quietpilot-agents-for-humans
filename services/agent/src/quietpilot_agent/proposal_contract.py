"""Provider-neutral proposal composition and trusted-input validation.

These rules do not invoke models or execute external actions. The runtime applies
this contract while coordinating its per-invocation agent graph.
"""

from __future__ import annotations

from collections import Counter

from .context import BoundedContext, canonical_action_key
from .models import (
    ActionProposal,
    CandidateProposal,
    CapabilityAssessment,
    CapabilityStatus,
    CasePlanProposal,
    CaseType,
    OrchestrationRequest,
    PlannerAssessment,
    ProposalStage,
    SignalAssessment,
    risk_at_least,
)

CANDIDATE_CASE_TYPES = {CaseType.CONNECTED_SIGNAL, CaseType.ROUTINE_DISCOVERY}


def _compose_signal_assessment(
    request: OrchestrationRequest,
    scope: BoundedContext,
) -> SignalAssessment:
    uncertainty = None
    if request.conflicting_evidence:
        uncertainty = "The sources conflict and need your decision."
    return SignalAssessment(
        summary=f"Reviewed {len(scope.evidence_revisions())} sources.",
        evidence_refs=request.evidence_refs,
        confidence=0.55 if request.conflicting_evidence else 0.9,
        uncertainty_reason=uncertainty,
    )


def _compose_capability_assessment(
    request: OrchestrationRequest,
    scope: BoundedContext,
) -> CapabilityAssessment:
    records = [scope.capabilities[item] for item in request.capability_ids]
    return CapabilityAssessment(
        available=[
            record.capability_id
            for record in records
            if record.status is CapabilityStatus.AVAILABLE
        ],
        inaccessible=[
            record.capability_id
            for record in records
            if record.status is CapabilityStatus.INACCESSIBLE
        ],
        missing=[
            record.capability_id
            for record in records
            if record.status is CapabilityStatus.MISSING
        ],
        notes=["Capability inventory is read-only and does not grant authority."],
    )


def _compose_output(
    request: OrchestrationRequest,
    scope: BoundedContext,
    trusted_actions: list[ActionProposal],
) -> CandidateProposal | CasePlanProposal:
    if _expects_candidate(request):
        raise ValueError("discovery proposals must use the action-ready planner")

    if request.conflicting_evidence:
        question = "The sources conflict. Which should the plan follow?"
    elif len(trusted_actions) != len(request.requested_actions):
        question = "Set up the required connection or access first?"
    else:
        question = "Review this plan and continue to approval?"

    return CasePlanProposal(
        case_type=request.case_type,
        goal=request.goal,
        explanation=(
            "No external action was taken. The plan uses only current sources and "
            "available actions and requires approval."
        ),
        evidence_revisions=scope.evidence_revisions(),
        preparation_steps=[
            "Source versions and available actions are rechecked before execution."
        ],
        actions=trusted_actions,
        decision_question=question,
    )


def _action_is_available(action: ActionProposal, scope: BoundedContext) -> bool:
    operation = f"{action.connector}.{action.verb}"
    return any(
        record.status is CapabilityStatus.AVAILABLE
        and record.connector == action.connector
        and operation in record.operations
        and set(record.required_scopes).issubset(action.required_scopes)
        for capability_id, record in scope.capabilities.items()
        if capability_id in scope.allowed_capability_ids
    )


def _trusted_actions(
    request: OrchestrationRequest,
    scope: BoundedContext,
) -> list[ActionProposal]:
    if request.conflicting_evidence:
        return []
    return [
        action
        for action in request.requested_actions
        if _action_is_available(action, scope)
        and risk_at_least(action.risk, request.risk)
    ]


def _validate_grounding(
    output: CandidateProposal | CasePlanProposal,
    request: OrchestrationRequest,
    scope: BoundedContext,
) -> None:
    if isinstance(output, CandidateProposal):
        if not _expects_candidate(request):
            raise ValueError("candidate output is invalid for this ingress")
        if output.outcome != request.goal:
            raise ValueError("candidate changed the trusted goal")
        if output.evidence_refs != request.evidence_refs:
            raise ValueError("candidate changed the trusted evidence set")
        if output.primary_group_hint != request.primary_group_hint:
            raise ValueError("candidate changed the trusted group hint")
        if output.tags != request.tags:
            raise ValueError("candidate changed the trusted tags")
        if output.fingerprint_inputs != [*request.evidence_refs, request.goal]:
            raise ValueError("candidate changed the trusted fingerprint inputs")
        if not set(output.required_capabilities).issubset(scope.allowed_capability_ids):
            raise ValueError("candidate capability is outside this invocation")
        if not risk_at_least(output.risk, request.risk):
            raise ValueError("candidate risk is below the trusted risk floor")
        return

    if _expects_candidate(request):
        raise ValueError("case plan output is invalid for this ingress")
    if output.case_type is not request.case_type:
        raise ValueError("case plan changed the trusted case type")
    if output.goal != request.goal:
        raise ValueError("case plan changed the trusted goal")
    expected_revisions = scope.evidence_revisions()
    if output.evidence_revisions != expected_revisions:
        raise ValueError("case plan evidence revisions are stale or incomplete")
    trusted_action_counts = Counter(
        canonical_action_key(action) for action in _trusted_actions(request, scope)
    )
    output_action_counts = Counter(
        canonical_action_key(action) for action in output.actions
    )
    if any(
        count > trusted_action_counts.get(action_key, 0)
        for action_key, count in output_action_counts.items()
    ):
        raise ValueError("case plan changed a trusted action envelope")
    for action in output.actions:
        if not _action_is_available(action, scope):
            raise ValueError("case plan includes an unavailable action")


def _expects_candidate(request: OrchestrationRequest) -> bool:
    return (
        request.proposal_stage is ProposalStage.DISCOVERY
        and request.case_type in CANDIDATE_CASE_TYPES
    )


def _validate_signal_assessment(
    payload: dict[str, object],
    request: OrchestrationRequest,
) -> SignalAssessment:
    output = SignalAssessment.model_validate(payload)
    if output.evidence_refs != request.evidence_refs:
        raise ValueError("signal analyst changed the evidence set")
    return output


def _validate_capability_assessment(
    payload: dict[str, object],
    request: OrchestrationRequest,
    scope: BoundedContext,
) -> CapabilityAssessment:
    output = CapabilityAssessment.model_validate(payload)
    expected = _compose_capability_assessment(request, scope)
    if (
        output.available != expected.available
        or output.inaccessible != expected.inaccessible
        or output.missing != expected.missing
    ):
        raise ValueError("capability analyst changed the trusted inventory")
    return output


def _validate_planner_assessment(
    payload: dict[str, object],
    request: OrchestrationRequest,
    scope: BoundedContext,
) -> PlannerAssessment:
    output = PlannerAssessment.model_validate(payload)
    proposal = output.candidate if output.candidate is not None else output.case_plan
    if proposal is None:
        raise ValueError("planner returned no proposal")
    _validate_grounding(proposal, request, scope)
    return output
