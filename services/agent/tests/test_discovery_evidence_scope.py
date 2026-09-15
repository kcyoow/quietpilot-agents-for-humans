from __future__ import annotations

from quietpilot_agent import DeterministicModelFactory, InMemoryContextRepository
from quietpilot_agent.discovery import (
    discover_action_ready_candidate,
    discover_action_ready_candidates,
)
from quietpilot_agent.local_model import DeterministicModel, ModelPlan
from quietpilot_agent.models import (
    CapabilityRecord,
    CapabilityStatus,
    CaseType,
    DiscoveryAssessment,
    EvidenceRecord,
    OrchestrationRequest,
    Risk,
)

REF_A = "mail:application"
REF_B = "mail:submission"


def _inputs():
    request = OrchestrationRequest(
        user_id="user-a",
        case_type=CaseType.CONNECTED_SIGNAL,
        goal="제출 준비",
        evidence_refs=[REF_A, REF_B],
        capability_ids=["quietpilot.task.prepare"],
        primary_group_hint="deadlines",
        risk=Risk.LOW,
    )
    repository = InMemoryContextRepository(
        evidence=[
            EvidenceRecord(
                user_id="user-a",
                ref=REF_A,
                revision=1,
                source="gmail",
                title="신청 마감 안내",
                facts=[],
            ),
            EvidenceRecord(
                user_id="user-a",
                ref=REF_B,
                revision=1,
                source="gmail",
                title="제출 마감 안내",
                facts=[],
            ),
        ],
        capabilities=[
            CapabilityRecord(
                user_id="user-a",
                capability_id="quietpilot.task.prepare",
                connector="quietpilot",
                status=CapabilityStatus.AVAILABLE,
                operations=["quietpilot.prepare_task"],
                required_scopes=[],
            )
        ],
    )
    return request, repository.open_scope(request)


class _SiblingEvidenceFactory(DeterministicModelFactory):
    """Force batch fallback, then cite sibling B when the current request is A."""

    def create(self, role: str, plan: ModelPlan) -> DeterministicModel:
        if role == "discovery_batch_planner":
            model = DeterministicModel(
                role,
                ModelPlan(
                    steps=plan.steps,
                    output=plan.output,
                    invalid_output_attempts=2,
                ),
            )
        elif role == "discovery_planner":
            assert isinstance(plan.output, DiscoveryAssessment)
            opportunity = plan.output.opportunity
            assert opportunity is not None
            output = plan.output
            if opportunity.evidence_refs == [REF_A]:
                action = opportunity.proposed_actions[0].model_copy(
                    update={
                        "parameters": {
                            **opportunity.proposed_actions[0].parameters,
                            "source_ref": REF_B,
                        }
                    }
                )
                output = output.model_copy(
                    update={
                        "opportunity": opportunity.model_copy(
                            update={
                                "evidence_refs": [REF_B],
                                "proposed_actions": [action],
                            }
                        )
                    }
                )
            model = DeterministicModel(role, ModelPlan(steps=plan.steps, output=output))
        else:
            return super().create(role, plan)
        self.models[role] = model
        return model


def test_individual_discovery_rejects_sibling_evidence_from_the_wider_batch_scope():
    request, batch_scope = _inputs()
    current = request.model_copy(update={"evidence_refs": [REF_A]})

    result = discover_action_ready_candidate(
        current, batch_scope, _SiblingEvidenceFactory()
    )

    assert result.validated is False
    assert result.candidate is None
    assert result.rejection_code == "evidence_scope"
    assert result.external_mutation_count == 0


def test_batch_fallback_does_not_mark_wrongly_grounded_evidence_as_resolved():
    request, scope = _inputs()

    result = discover_action_ready_candidates(request, scope, _SiblingEvidenceFactory())

    assert result.validated is True
    assert result.unresolved_evidence_count == 1
    assert [candidate.evidence_refs for candidate in result.candidates] == [[REF_B]]
    assert result.external_mutation_count == 0


def test_individual_discovery_can_use_its_own_evidence_with_a_wider_batch_scope():
    request, batch_scope = _inputs()
    current = request.model_copy(update={"evidence_refs": [REF_A]})

    result = discover_action_ready_candidate(
        current, batch_scope, DeterministicModelFactory()
    )

    assert result.validated is True
    assert result.candidate is not None
    assert result.candidate.evidence_refs == [REF_A]
    assert result.external_mutation_count == 0
