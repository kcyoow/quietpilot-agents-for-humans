from __future__ import annotations

import json

import pytest
from quietpilot_agent import InMemoryContextRepository
from quietpilot_agent.discovery import discover_action_ready_candidates
from quietpilot_agent.discovery_copy import CopyTemporalError, validate_temporal_copy
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


def _inputs():
    evidence = EvidenceRecord(
        user_id="synthetic-owner",
        ref="mail:synthetic-deadline",
        revision=1,
        source="gmail",
        title="연구 발표 제출 마감",
        facts=["received_at_unix_ms=1788825600000"],
        untrusted_text="제출 마감은 2026년 10월 3일 오후 8시 EDT입니다. 메일 발송 당시 안내: 5일 남음.",
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
    return request, repository.open_scope(request)


class _TemporalFactory:
    def __init__(self, field, text, *, repair=False):
        self.field = field
        self.text = text
        self.repair = repair
        self.models = []

    def create(self, role, plan):
        original = plan.output
        opportunity = (
            original.opportunities[0]
            if isinstance(original, DiscoveryBatchAssessment)
            else original.opportunity
        )
        assert opportunity is not None
        if self.field == "title":
            action = opportunity.proposed_actions[0]
            bad_opportunity = opportunity.model_copy(
                update={
                    "proposed_actions": [
                        action.model_copy(
                            update={
                                "parameters": {**action.parameters, "title": self.text}
                            }
                        )
                    ]
                }
            )
        else:
            bad_opportunity = opportunity.model_copy(update={self.field: self.text})
        bad = original.model_copy(
            update={"opportunities": [bad_opportunity]}
            if isinstance(original, DiscoveryBatchAssessment)
            else {"opportunity": bad_opportunity}
        )
        repair = self.repair

        class _TemporalModel(DeterministicModel):
            async def stream(
                self, messages, tool_specs=None, system_prompt=None, **kwargs
            ):
                self.plan = ModelPlan(
                    steps=plan.steps,
                    output=original if repair and self.output_attempts else bad,
                )
                async for event in super().stream(
                    messages, tool_specs, system_prompt, **kwargs
                ):
                    yield event

        model = _TemporalModel(role, plan)
        self.models.append(model)
        return model


@pytest.mark.parametrize("field", ["outcome", "summary", "why_now", "title"])
def test_current_countdown_never_becomes_persistable_candidate_copy(field):
    request, scope = _inputs()
    result = discover_action_ready_candidates(
        request,
        scope,
        _TemporalFactory(
            field, "제출 마감은 2026년 10월 3일이며 현재부터 약 5일 남았어요."
        ),
    )
    assert result.candidates == []
    assert result.unresolved_evidence_count == 1


def test_relative_copy_can_be_repaired_inside_the_same_sdk_context():
    request, scope = _inputs()
    factory = _TemporalFactory(
        "why_now", "제출 마감이 임박해 서둘러야 해요.", repair=True
    )
    result = discover_action_ready_candidates(request, scope, factory)
    assert len(result.candidates) == 1 and result.unresolved_evidence_count == 0
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
    assert "opportunities -> 0 -> why_now" in json.dumps(errors)


@pytest.mark.parametrize(
    "text",
    [
        "현재 기준으로 약 5일이에요.",
        "제출 마감까지 3일이 채 남지 않았어요.",
        "5일 뒤 마감이에요.",
        "마감은 5일 후예요.",
        "앞으로 이틀 남았어요.",
        "내일 오후에 제출해요.",
        "다음 주 월요일에 마감돼요.",
        "다음주월요일까지 준비해요.",
        "3 days left before the deadline.",
        "The deadline is in about five days.",
    ],
)
def test_persisted_current_relative_dates_are_rejected_even_if_repeated_in_source(text):
    with pytest.raises(CopyTemporalError, match="^copy_temporal_relative$"):
        validate_temporal_copy(text, evidence_texts=(text,))


@pytest.mark.parametrize(
    "text",
    [
        "제출 마감이 임박했어요.",
        "기한이 촉박해요.",
        "마감이 얼마 남지 않아 서둘러야 해요.",
        "다가오는 행사 준비를 서둘러요.",
    ],
)
def test_date_derived_current_urgency_is_not_persistable(text):
    with pytest.raises(CopyTemporalError, match="^copy_temporal_urgency$"):
        validate_temporal_copy(text, evidence_texts=())


@pytest.mark.parametrize(
    "text",
    [
        "제출 기한은 2026년 10월 3일 오후 8시 EDT예요.",
        "마감 전에 끝낼 일을 정리해요.",
        "자료 검토에는 2시간이 소요돼요.",
        "행사 하루 전 알림을 준비해요.",
        "로그인 5분 후 자동 로그아웃돼요.",
        "계정 침해가 확인되어 즉시 접근 권한을 확인해야 해요.",
        "시급한 계정 보안 대응이 필요해요.",
        "내일배움카드 신청 서류를 준비해요.",
    ],
)
def test_absolute_dates_durations_offsets_and_direct_security_urgency_remain_available(
    text,
):
    validate_temporal_copy(text, evidence_texts=(text,))


@pytest.mark.parametrize(
    "text",
    [
        "메일 발송 당시 ‘5일 남음’이라고 안내했어요.",
        "메일 발송 당시 '5일 남음'이라고 안내했어요.",
        "메일을 받은 시점에는 ‘5일 남음’으로 안내되어 있었어요.",
        "원문 작성 당시 “3 days left.”라는 안내였어요.",
        "원문 작성 당시 '3 days left.'라는 안내였어요.",
        "메일(https://mail.example.invalid/notice) 발송 당시 ‘5일 남음’이라고 안내했어요.",
    ],
)
def test_exact_source_time_quotations_survive_without_becoming_a_current_clock(text):
    validate_temporal_copy(text, evidence_texts=("원문: 5일 남음. 3 days left.",))


@pytest.mark.parametrize(
    "text,sources",
    [
        ("‘5일 남음’이라고 안내해요.", ("5일 남음",)),
        ("메일 발송 당시 ‘5일 남음’이라고 안내했어요.", ("4일 남음",)),
        (
            "메일 발송 당시 ‘5일 남음’이라고 안내했어요. ‘내일’이 마감이에요.",
            ("5일 남음. 내일",),
        ),
        (
            "메일 발송 당시 ‘5일 남음’이라고 안내했지만 현재는 6일 남았어요.",
            ("5일 남음",),
        ),
        ("At the time of the meeting, '3 days left' was shown.", ("3 days left",)),
        ("'5일 남음'이라고 안내했어요.", ("5일 남음",)),
        ("메일 발송 당시 '5일 남음'이라고 안내했어요.", ("4일 남음",)),
        (
            "메일 발송 당시 '5일 남음'이라고 안내했지만 현재는 6일 남았어요.",
            ("5일 남음",),
        ),
        (
            "메일 발송 당시 '5일 남음'에 관한 안내였어요. 내일이 마감이에요.",
            ("5일 남음",),
        ),
        ("메일 발송 당시 '3 days left's 안내였어요.", ("3 days left",)),
    ],
)
def test_quotation_needs_exact_source_and_local_source_time_attribution(text, sources):
    with pytest.raises(CopyTemporalError):
        validate_temporal_copy(text, evidence_texts=sources)


@pytest.mark.parametrize(
    "text",
    [
        "When the email was sent, it said ‘5일 남음’.",
        "When the email was sent, it said '5일 남음'.",
    ],
)
@pytest.mark.parametrize("field", ["summary", "why_now"])
def test_source_time_quotation_is_accepted_by_real_discovery_sdk(text, field):
    request, scope = _inputs()
    factory = _TemporalFactory(field, text)
    result = discover_action_ready_candidates(request, scope, factory)
    assert len(result.candidates) == 1 and result.unresolved_evidence_count == 0
    assert len(factory.models) == 1 and factory.models[0].output_attempts == 1


def test_temporal_failure_diagnostics_keep_only_static_rules_and_paths(capsys):
    request, scope = _inputs()
    private = "SYNTHETIC-PRIVATE-TIME-MARKER"
    factory = _TemporalFactory("summary", f"마감까지 5일 남았어요. {private}")
    result = discover_action_ready_candidates(request, scope, factory)
    assert result.candidates == [] and result.unresolved_evidence_count == 1
    assert len(factory.models) == 4
    assert all(model.output_attempts == 2 for model in factory.models)
    logs = capsys.readouterr().out
    assert private not in logs and request.evidence_refs[0] not in logs
    assert '"rejection_code":"copy_temporal_relative"' in logs
    assert all(
        json.loads(model.received_messages[0][0]["content"][0]["text"])[
            "previous_rejection"
        ]["code"]
        == "copy_temporal_relative"
        for model in factory.models[1:]
    )


@pytest.mark.parametrize(
    "text",
    [
        "제출 기한이 2030-05-01로 명시되어 있으며, 현재부터 기한까지 시간이 부족하지 않지만 미리 준비하는 것이 좋습니다.",
        "준비할 시간이 충분해요.",
        "마감까지 시간 여유가 있어요.",
        "남은 기간이 넉넉한 편이에요.",
        "There is enough time left before the deadline.",
    ],
)
def test_time_sufficiency_assessments_are_not_persisted(text):
    with pytest.raises(CopyTemporalError, match="^copy_temporal_adequacy$"):
        validate_temporal_copy(text, evidence_texts=(text,))


def test_time_sufficiency_is_repaired_inside_sdk_with_specific_feedback():
    request, scope = _inputs()
    factory = _TemporalFactory(
        "why_now", "현재부터 기한까지 시간이 부족하지 않아요.", repair=True
    )
    result = discover_action_ready_candidates(request, scope, factory)
    assert len(result.candidates) == 1 and result.unresolved_evidence_count == 0
    assert len(factory.models) == 1 and factory.models[0].output_attempts == 2
    assert "copy_temporal_adequacy" in json.dumps(factory.models[0].received_messages)


def test_source_time_quote_can_preserve_an_authors_original_time_assessment():
    text = "메일 발송 당시 ‘준비할 시간이 충분해요’라고 안내했어요."
    validate_temporal_copy(text, evidence_texts=("준비할 시간이 충분해요",))


@pytest.mark.parametrize(
    "text",
    [
        "- 시간이 부족하면 담당자에게 알려 주세요.",
        "시간이 충분하다면 제출 자료를 정리해 주세요.",
        "시간이 부족한 경우에는 연장을 요청해 주세요.",
        "남은 시간 여유가 없으면 담당자에게 알려 주세요.",
        "If time is short, request an extension.",
        "If there is enough time left, review the attachment.",
    ],
)
def test_explicit_conditional_time_instructions_are_not_current_assessments(text):
    validate_temporal_copy(text, evidence_texts=(text,))


@pytest.mark.parametrize(
    "text",
    [
        "시간이 부족하면 담당자에게 알려 주세요. 현재 시간이 부족합니다.",
        "현재 시간이 부족합니다. 시간이 부족하면 담당자에게 알려 주세요.",
        "시간이 부족하면 담당자에게 알려 주세요, 현재 시간이 부족합니다.",
        "시간이 부족하므로 담당자에게 알려 주세요.",
        "시간이 부족하지만 담당자에게 알려 주세요.",
        "마감까지 여유가 없지만 남은 시간 여유가 없으면 담당자에게 알려 주세요.",
        "남은 시간 여유가 없으면 요청해 주세요, 현재 시간 여유가 없어요.",
        "If time is short, request an extension. Time is insufficient.",
        "If time is short, request an extension; time is short.",
        "If you need help, time is short.",
    ],
)
def test_conditional_instruction_does_not_exempt_separate_time_assertions(text):
    with pytest.raises(CopyTemporalError, match="^copy_temporal_adequacy$"):
        validate_temporal_copy(text, evidence_texts=(text,))


def test_conditional_instruction_does_not_exempt_a_current_countdown():
    text = "시간이 부족하면 담당자에게 알려 주세요. 현재부터 3일 남았어요."
    with pytest.raises(CopyTemporalError, match="^copy_temporal_relative$"):
        validate_temporal_copy(text, evidence_texts=(text,))


def test_conditional_time_instruction_survives_sdk_and_final_grounding():
    request, scope = _inputs()
    text = "If time is short, contact the organizer."
    scope.evidence[request.evidence_refs[0]].untrusted_text += " " + text
    factory = _TemporalFactory("summary", text)
    result = discover_action_ready_candidates(request, scope, factory)
    assert len(result.candidates) == 1 and result.unresolved_evidence_count == 0
    assert result.candidates[0].summary == text
    assert len(factory.models) == 1 and factory.models[0].output_attempts == 1
