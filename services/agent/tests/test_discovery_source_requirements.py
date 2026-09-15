from __future__ import annotations

import json

import pytest
from quietpilot_agent import InMemoryContextRepository
from quietpilot_agent.discovery import (
    _ground_assessment,
    discover_action_ready_candidates,
)
from quietpilot_agent.local_model import DeterministicModel, ModelPlan
from quietpilot_agent.models import (
    ActionProposal,
    CapabilityRecord,
    CapabilityStatus,
    CaseType,
    DiscoveredOpportunity,
    DiscoveryAssessment,
    DiscoveryBatchAssessment,
    DiscoveryDisposition,
    EvidenceRecord,
    OpportunityType,
    OrchestrationRequest,
    ProposalStage,
    Risk,
)


def inputs(body, title="서비스 안내", *, other_body=None):
    records = [
        EvidenceRecord(
            user_id="synthetic-owner",
            ref="mail:primary",
            revision=1,
            source="gmail",
            title=title,
            untrusted_text=body,
        )
    ]
    if other_body:
        records.append(
            EvidenceRecord(
                user_id="synthetic-owner",
                ref="mail:other",
                revision=1,
                source="gmail",
                title="별도 업무 요청",
                untrusted_text=other_body,
            )
        )
    capabilities = [
        CapabilityRecord(
            user_id="synthetic-owner",
            capability_id=f"quietpilot.{verb}",
            connector="quietpilot",
            status=CapabilityStatus.AVAILABLE,
            operations=[f"quietpilot.{verb}"],
        )
        for verb in ("prepare_reply", "prepare_task", "prepare_reminder")
    ]
    request = OrchestrationRequest(
        user_id="synthetic-owner",
        case_type=CaseType.CONNECTED_SIGNAL,
        goal="필요한 Prepare follow-up",
        evidence_refs=[record.ref for record in records],
        capability_ids=[cap.capability_id for cap in capabilities],
        primary_group_hint="follow-ups",
        risk=Risk.LOW,
    )
    return request, InMemoryContextRepository(
        evidence=records, capabilities=capabilities
    ).open_scope(request)


def opportunity(*, kind=OpportunityType.FOLLOW_UP, verb="prepare_reply", refs=None):
    return DiscoveredOpportunity(
        outcome="Prepare the requested response",
        summary="Prepare the follow-up supported by the source.",
        why_now="The request supports preparing a response.",
        opportunity_type=kind,
        evidence_refs=refs or ["mail:primary"],
        confidence=0.85,
        primary_group_hint="follow-ups",
        tags=[],
        risk=Risk.LOW,
        proposed_actions=[
            ActionProposal(
                connector="quietpilot",
                target_resource="case:source-check",
                verb=verb,
                parameters={"source_ref": "mail:primary", "title": "Prepare follow-up"},
                required_scopes=[],
                risk=Risk.LOW,
                reversible=True,
                verification_method="case_plan_readback",
            )
        ],
    )


class Factory:
    def __init__(self, *, kind=OpportunityType.FOLLOW_UP, repair=None, refs=None):
        self.kind, self.repair, self.refs = kind, repair, refs
        self.models = []

    def create(self, role, plan):
        is_batch = isinstance(plan.output, DiscoveryBatchAssessment)
        refs = plan.steps[0].input["refs"]
        initial = opportunity(
            kind=self.kind,
            verb="prepare_task"
            if self.kind is OpportunityType.DEADLINE
            else "prepare_reminder"
            if self.kind is OpportunityType.APPOINTMENT
            else "prepare_reply",
            refs=self.refs,
        )

        def output(repair):
            selected = (
                None
                if repair == "suppress"
                else opportunity(verb="prepare_task")
                if repair == "task"
                else initial
            )
            if is_batch:
                return DiscoveryBatchAssessment(
                    assessed_evidence_refs=refs,
                    reason="Reviewed each source.",
                    opportunities=[selected] if selected else [],
                )
            return DiscoveryAssessment(
                disposition=DiscoveryDisposition.PROPOSE
                if selected
                else DiscoveryDisposition.SUPPRESS,
                reason="Reviewed the source.",
                opportunity=selected,
            )

        repair_kind = self.repair

        class Model(DeterministicModel):
            async def stream(
                self, messages, tool_specs=None, system_prompt=None, **kwargs
            ):
                self.plan = ModelPlan(
                    steps=plan.steps,
                    output=output(repair_kind if self.output_attempts else None),
                )
                async for event in super().stream(
                    messages, tool_specs, system_prompt, **kwargs
                ):
                    yield event

        model = Model(role, plan)
        self.models.append(model)
        return model


NEGATIVE_REPLY = [
    (
        "Budget notification",
        "If you do not recognize the account above, disregard this email or contact Support. You requested that we alert you when forecasted cost exceeds a threshold. Find details in the dashboard.",
    ),
    (
        "자동 비용 안내",
        "사용자가 요청한 비용 알림입니다. 문의 사항이 있으면 이 메일에 회신해 주세요.",
    ),
    ("알림", "Please do not reply to this email. This mailbox is not monitored."),
    (
        "접수 안내",
        "Your request was received. We will reply when it has been processed.",
    ),
    ("보안 알림", "Please review your security settings and account activity."),
    (
        "Account notice",
        "If this activity was not yours, contact Support. If you have questions, please reply to this email.",
    ),
    ("인증 안내", "Please confirm your email address using the verification button."),
    ("업무 안내", "Thank you for your reply. No response is required."),
]


@pytest.mark.parametrize("title,body", NEGATIVE_REPLY)
def test_reply_candidate_requires_a_real_recipient_response_request(
    title, body, capsys
):
    request, scope = inputs(body, title)
    factory = Factory()
    result = discover_action_ready_candidates(request, scope, factory)
    assert result.candidates == [] and result.unresolved_evidence_count == 1
    assert len(factory.models) == 4 and all(
        model.output_attempts == 2 for model in factory.models
    )
    logs = capsys.readouterr().out
    assert '"rejection_code":"reply_request_missing"' in logs
    assert body not in logs


@pytest.mark.parametrize(
    "title,body",
    [
        ("회신 요청", "보고서 검토 의견을 알려주세요."),
        ("업무 요청", "자료를 확인한 후 회신 부탁드립니다."),
        ("검토 의견", "보고서 초안을 검토하고 의견을 알려주시면 감사하겠습니다."),
        ("업무 요청", "Could you please reply with your comments?"),
        ("업무 요청", "Please reply by email with your availability."),
        ("업무 요청", "Please confirm your attendance."),
        ("업무 요청", "Please review the report and reply with feedback."),
        ("업무 요청", "Please send us the report draft."),
        ("업무 요청", "Could you confirm receipt of the report?"),
        ("업무 요청", "Please provide your feedback on the proposal."),
        ("참석 확인", "If you can attend, please reply with your availability."),
    ],
)
def test_genuine_reply_and_human_work_requests_are_preserved(title, body):
    request, scope = inputs(body, title)
    factory = Factory()
    result = discover_action_ready_candidates(request, scope, factory)
    assert len(result.candidates) == 1 and result.unresolved_evidence_count == 0
    assert len(factory.models) == 1 and factory.models[0].output_attempts == 1


@pytest.mark.parametrize("repair", ["suppress", "task"])
def test_invalid_reply_can_be_repaired_without_more_model_calls(repair):
    request, scope = inputs(
        "Please review the figures in the report. No reply is required."
    )
    factory = Factory(repair=repair)
    result = discover_action_ready_candidates(request, scope, factory)
    assert result.unresolved_evidence_count == 0
    assert len(factory.models) == 1 and factory.models[0].output_attempts == 2
    assert "reply_request_missing" in json.dumps(factory.models[0].received_messages)
    assert len(result.candidates) == (1 if repair == "task" else 0)
    if result.candidates:
        assert result.candidates[0].proposed_actions[0].verb == "prepare_task"


def test_other_evidence_cannot_supply_the_reply_request_for_an_automatic_notice():
    request, scope = inputs(
        "Automatic forecast notification. Details are in the dashboard.",
        other_body="Please reply with your comments.",
    )
    result = discover_action_ready_candidates(
        request, scope, Factory(refs=["mail:primary", "mail:other"])
    )
    assert result.candidates == []
    assert result.unresolved_evidence_count >= 1


def test_plain_login_notice_cannot_be_labeled_as_a_deadline():
    request, scope = inputs(
        "A sign-in to your account was detected. Review activity if it was not yours.",
        "새 로그인 알림",
    )
    factory = Factory(kind=OpportunityType.DEADLINE)
    result = discover_action_ready_candidates(request, scope, factory)
    assert result.candidates == [] and result.unresolved_evidence_count == 1


@pytest.mark.parametrize(
    "body",
    [
        "신청서 제출 기한은 2030-05-01입니다.",
        "Please submit the application form.",
        "Payment is due on 2030-05-01.",
        "계정 삭제 예정일은 2030-05-01입니다.",
        "Start your submission draft. Sep 17, 8PM EDT is a hard cutoff.",
        "The submission cutoff is September 17 at 8PM EDT.",
        "The registration cut-off is September 17 at 8PM EDT.",
    ],
)
def test_explicit_deadline_or_submission_obligation_remains_supported(body):
    request, scope = inputs(body)
    result = discover_action_ready_candidates(
        request, scope, Factory(kind=OpportunityType.DEADLINE)
    )
    assert len(result.candidates) == 1


def test_registered_event_final_call_reuses_the_existing_source_deadline_proof():
    request, scope = inputs(
        "There are only 3 days left to complete your submission for Sample Agents Hackathon. "
        "You received this email because you signed up for Sample Agents Hackathon.",
        "Final call for submissions - Sample Agents Hackathon",
    )
    result = discover_action_ready_candidates(
        request, scope, Factory(kind=OpportunityType.DEADLINE)
    )
    assert len(result.candidates) == 1 and result.unresolved_evidence_count == 0


def test_generic_final_call_without_an_existing_obligation_does_not_borrow_proof():
    request, scope = inputs(
        "Explore our public event. You signed up for our newsletter.",
        "Final call for submissions - Sample Agents Hackathon",
    )
    result = discover_action_ready_candidates(
        request, scope, Factory(kind=OpportunityType.DEADLINE)
    )
    assert result.candidates == []


@pytest.mark.parametrize(
    "kind,body,error",
    [
        (
            OpportunityType.FOLLOW_UP,
            "Automatic budget notification.",
            "reply preparation has no explicit source response request",
        ),
        (
            OpportunityType.DEADLINE,
            "A new sign-in was detected.",
            "deadline opportunity has no source deadline or obligation",
        ),
    ],
)
def test_final_grounding_repeats_source_requirements(kind, body, error):
    request, scope = inputs(body)
    assessment = DiscoveryAssessment(
        disposition=DiscoveryDisposition.PROPOSE,
        reason="검토했어요.",
        opportunity=opportunity(
            kind=kind,
            verb="prepare_task"
            if kind is OpportunityType.DEADLINE
            else "prepare_reply",
        ),
    )
    with pytest.raises(ValueError, match=error):
        _ground_assessment(assessment, request, scope)


PUBLIC_PROMOTION = (
    "We would love to have you join our public technology conference. "
    "Explore the sessions and keynote speakers. If you're planning to attend, "
    "register here for a conference pass. Registration deadline: 2030-10-01. "
    "Please reply to receive promotional ticket details."
)


@pytest.mark.parametrize("kind", list(OpportunityType))
def test_public_event_promotion_cannot_be_relabelled_into_automatic_work(kind, capsys):
    request, scope = inputs(PUBLIC_PROMOTION, "Explore our technology sessions")
    factory = Factory(kind=kind)
    result = discover_action_ready_candidates(request, scope, factory)
    assert result.candidates == [] and result.unresolved_evidence_count == 1
    assert len(factory.models) == 4 and all(
        model.output_attempts == 2 for model in factory.models
    )
    logs = capsys.readouterr().out
    assert '"rejection_code":"public_event_promotion"' in logs
    assert PUBLIC_PROMOTION not in logs


def test_public_promotion_can_be_explicitly_suppressed_in_the_same_sdk_repair_context():
    request, scope = inputs(PUBLIC_PROMOTION)
    factory = Factory(kind=OpportunityType.APPOINTMENT, repair="suppress")
    result = discover_action_ready_candidates(request, scope, factory)
    assert result.candidates == [] and result.unresolved_evidence_count == 0
    assert len(factory.models) == 1 and factory.models[0].output_attempts == 2
    assert "public_event_promotion" in json.dumps(factory.models[0].received_messages)


@pytest.mark.parametrize("kind", list(OpportunityType))
def test_final_gate_repeats_public_promotion_rule_for_every_label(kind):
    request, scope = inputs(PUBLIC_PROMOTION)
    assessment = DiscoveryAssessment(
        disposition=DiscoveryDisposition.PROPOSE,
        reason="검토했어요.",
        opportunity=opportunity(kind=kind, verb="prepare_task"),
    )
    with pytest.raises(ValueError, match="public event promotion"):
        _ground_assessment(assessment, request, scope)


@pytest.mark.parametrize(
    "title,body",
    [
        ("일정 안내", "다음 주에는 평소 만나던 곳에서 함께 업무를 정리하기로 했어요."),
        (
            "Booking confirmed",
            "Your reservation has been confirmed. " + PUBLIC_PROMOTION,
        ),
        (
            "Booking changed",
            "Your appointment has been rescheduled. " + PUBLIC_PROMOTION,
        ),
        (
            "Booking cancelled",
            "Your reservation has been cancelled. " + PUBLIC_PROMOTION,
        ),
        (
            "Registration confirmation",
            "Your registration is confirmed. " + PUBLIC_PROMOTION,
        ),
        (
            "참가 등록 확인",
            "신청하신 행사의 등록이 완료되었습니다. " + PUBLIC_PROMOTION,
        ),
        (
            "Team planning meeting invitation",
            "Please join our team meeting to review the conference plan. If you can attend, register here for the internal meeting.",
        ),
        (
            "프로젝트 회의 초대",
            "컨퍼런스 발표 계획을 검토하는 팀 회의에 초대합니다. 참석할 예정이라면 회의 링크에서 등록하기를 눌러 주세요.",
        ),
    ],
)
def test_real_appointments_changes_cancellations_and_personal_work_invitations_survive(
    title, body
):
    request, scope = inputs(body, title)
    factory = Factory(kind=OpportunityType.APPOINTMENT)
    result = discover_action_ready_candidates(request, scope, factory)
    assert len(result.candidates) == 1 and result.unresolved_evidence_count == 0
    assert len(factory.models) == 1 and factory.models[0].output_attempts == 1


@pytest.mark.parametrize(
    "body",
    [
        PUBLIC_PROMOTION
        + " If your registration is confirmed, your pass will arrive later.",
        PUBLIC_PROMOTION + " Your registration is not confirmed.",
        PUBLIC_PROMOTION + " Your registration is confirmed?",
        PUBLIC_PROMOTION + " You signed up for our newsletter.",
        "공개 컨퍼런스의 세션과 연사를 소개합니다. 참석할 계획이라면 티켓을 구매하세요. 지금 등록하기를 누르세요.",
    ],
)
def test_conditional_confirmation_or_personalized_marketing_is_not_attendance_proof(
    body,
):
    request, scope = inputs(body)
    factory = Factory(kind=OpportunityType.APPOINTMENT, repair="suppress")
    result = discover_action_ready_candidates(request, scope, factory)
    assert result.candidates == [] and result.unresolved_evidence_count == 0
    assert factory.models[0].output_attempts == 2


def test_other_evidence_does_not_authorize_a_promotional_source_action():
    request, scope = inputs(
        PUBLIC_PROMOTION, other_body="Your unrelated reservation is confirmed."
    )
    assessment = DiscoveryAssessment(
        disposition=DiscoveryDisposition.PROPOSE,
        reason="검토했어요.",
        opportunity=opportunity(
            kind=OpportunityType.APPOINTMENT,
            verb="prepare_reminder",
            refs=["mail:primary", "mail:other"],
        ),
    )
    with pytest.raises(ValueError, match="public event promotion"):
        _ground_assessment(assessment, request, scope)


def test_user_selected_case_planning_is_not_changed_by_automatic_promotion_gate():
    request, scope = inputs(PUBLIC_PROMOTION)
    request = request.model_copy(update={"proposal_stage": ProposalStage.CASE_PLANNING})
    factory = Factory(kind=OpportunityType.APPOINTMENT)
    result = discover_action_ready_candidates(request, scope, factory)
    assert len(result.candidates) == 1 and result.unresolved_evidence_count == 0
    assert factory.models[0].output_attempts == 1
