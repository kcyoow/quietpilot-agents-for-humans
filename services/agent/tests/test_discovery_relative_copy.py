from __future__ import annotations

import json

import pytest
from quietpilot_agent import InMemoryContextRepository
from quietpilot_agent.discovery import discover_action_ready_candidates
from quietpilot_agent.discovery_copy import (
    CopyTemporalError,
    validate_display_copy,
    validate_temporal_copy,
)
from quietpilot_agent.local_model import DeterministicModel, ModelPlan
from quietpilot_agent.models import (
    CapabilityRecord,
    CapabilityStatus,
    CaseType,
    DiscoveryBatchAssessment,
    EvidenceRecord,
    OrchestrationRequest,
    Risk,
)

_SOURCE = "Submission closes on October 3, 2026, at 8 PM EDT. 7 hours remaining."
_ATTRIBUTED_QUOTE = "When the email was sent, it said ‘7 hours remaining.’."


@pytest.mark.parametrize(
    "text",
    [
        "메일 발송 당시 7시간 남았다고 안내했어요.",
        "메일 발송 당시 ‘7시간 남음’이라고 안내했어요.",
    ],
)
def test_attributed_translation_is_not_an_exact_source_quotation(text):
    validate_display_copy(text, evidence_texts=(_SOURCE,))
    with pytest.raises(CopyTemporalError, match="^copy_temporal_relative$"):
        validate_temporal_copy(text, evidence_texts=(_SOURCE,))


def test_original_language_quote_is_compatible_with_korean_display_copy():
    validate_display_copy(_ATTRIBUTED_QUOTE, evidence_texts=(_SOURCE,), language="en")
    validate_temporal_copy(_ATTRIBUTED_QUOTE, evidence_texts=(_SOURCE,))


@pytest.mark.parametrize(
    "text,code",
    [
        ("메일 발송 당시 ‘8 hours remaining.’이라고 안내했어요.", "relative"),
        ("‘7 hours remaining.’이라고 안내했어요.", "relative"),
        (
            "메일 발송 당시 안내예요. ‘7 hours remaining.’이라고 적혀 있어요.",
            "relative",
        ),
        (_ATTRIBUTED_QUOTE + " 현재 7시간 남았어요.", "relative"),
        (_ATTRIBUTED_QUOTE + " 내일이 마감이에요.", "relative"),
        (_ATTRIBUTED_QUOTE + " 제출 마감이 임박했어요.", "urgency"),
    ],
)
def test_attributed_quote_does_not_exempt_other_assertions_or_changed_values(
    text, code
):
    with pytest.raises(CopyTemporalError, match=f"^copy_temporal_{code}$"):
        validate_temporal_copy(text, evidence_texts=(_SOURCE,))


class _SourceTimeRepairFactory:
    def __init__(self):
        self.models = []

    def create(self, role, plan):
        original = plan.output
        batch = isinstance(original, DiscoveryBatchAssessment)
        opportunity = original.opportunities[0] if batch else original.opportunity
        assert opportunity is not None

        def output(text):
            revised = opportunity.model_copy(update={"summary": text, "why_now": text})
            return original.model_copy(
                update={"opportunities": [revised]}
                if batch
                else {"opportunity": revised}
            )

        rejected = output("메일 발송 당시 7시간 남았다고 안내했어요.")
        corrected = output(_ATTRIBUTED_QUOTE)

        class _SourceTimeModel(DeterministicModel):
            async def stream(
                self, messages, tool_specs=None, system_prompt=None, **kwargs
            ):
                self.plan = ModelPlan(
                    steps=plan.steps,
                    output=corrected if self.output_attempts else rejected,
                )
                async for event in super().stream(
                    messages, tool_specs, system_prompt, **kwargs
                ):
                    yield event

        model = _SourceTimeModel(role, plan)
        self.models.append(model)
        return model


def test_sdk_repairs_attributed_translation_with_original_source_quote_in_both_fields():
    evidence = EvidenceRecord(
        user_id="synthetic-owner",
        ref="mail:synthetic-relative-translation",
        revision=1,
        source="gmail",
        title="연구 발표 제출 마감",
        facts=["received_at_unix_ms=1788825600000"],
        untrusted_text=_SOURCE,
    )
    capability = CapabilityRecord(
        user_id=evidence.user_id,
        capability_id="quietpilot.task.prepare",
        connector="quietpilot",
        status=CapabilityStatus.AVAILABLE,
        operations=["quietpilot.prepare_task"],
        required_scopes=[],
    )
    request = OrchestrationRequest(
        user_id=evidence.user_id,
        case_type=CaseType.CONNECTED_SIGNAL,
        goal="제출 준비",
        evidence_refs=[evidence.ref],
        capability_ids=[capability.capability_id],
        primary_group_hint="deadlines",
        risk=Risk.LOW,
    )
    repository = InMemoryContextRepository(
        evidence=[evidence], capabilities=[capability]
    )
    factory = _SourceTimeRepairFactory()
    result = discover_action_ready_candidates(
        request, repository.open_scope(request), factory
    )

    assert len(result.candidates) == 1 and result.unresolved_evidence_count == 0
    assert result.candidates[0].summary == _ATTRIBUTED_QUOTE
    assert result.candidates[0].why_now == _ATTRIBUTED_QUOTE
    assert len(factory.models) == 1
    model = factory.models[0]
    assert model.output_attempts == 2
    assert model.tool_calls.count("read_evidence_context") == 1
    errors = [
        block["toolResult"]
        for message in model.received_messages[-1]
        for block in message["content"]
        if block.get("toolResult", {}).get("status") == "error"
    ]
    feedback = json.dumps(errors)
    assert "copy_temporal_relative" in feedback
    assert "summary" in feedback and "why_now" in feedback
