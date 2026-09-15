from __future__ import annotations

import json

import pytest
from quietpilot_agent import InMemoryContextRepository
from quietpilot_agent.context import ContextAccessDenied
from quietpilot_agent.local_model import DeterministicModel, ModelPlan, ToolStep
from quietpilot_agent.local_preparation import (
    LocalPreparationOutputError,
    LocalPreparationResult,
    LocalPreparationSourceUnavailable,
    prepare_local_artifact,
)
from quietpilot_agent.models import (
    ActionProposal,
    CapabilityRecord,
    CapabilityStatus,
    CaseType,
    EvidenceRecord,
    OrchestrationRequest,
    Risk,
)

BODY = "신청서와 재학증명서를 준비하여 2030-05-01까지 제출해 주세요. 신청서는 지정된 양식을 사용합니다."


def inputs(verb="prepare_task", body=BODY, *, direct=None, facts=None):
    records = [
        EvidenceRecord(
            user_id="synthetic-owner",
            ref="gmail:local-source",
            revision=1,
            source="gmail",
            title="신청 자료 안내",
            facts=facts or [],
            untrusted_text=body,
        )
    ]
    if direct is not None:
        records.append(
            EvidenceRecord(
                user_id="synthetic-owner",
                ref="message:local-user",
                revision=1,
                source="direct",
                title="사용자 요청",
                untrusted_text=direct,
            )
        )
    action = ActionProposal(
        connector="quietpilot",
        verb=verb,
        target_resource="case:local-preparation",
        parameters={"source_ref": records[0].ref, "title": "문안 준비"},
        required_scopes=[],
        risk=Risk.LOW,
        reversible=True,
        verification_method="case_plan_readback",
    )
    capability = CapabilityRecord(
        user_id=records[0].user_id,
        capability_id="quietpilot.local.prepare",
        connector="quietpilot",
        status=CapabilityStatus.AVAILABLE,
        operations=[f"quietpilot.{verb}"],
        required_scopes=[],
    )
    request = OrchestrationRequest(
        user_id=records[0].user_id,
        case_type=CaseType.CONNECTED_SIGNAL,
        goal="원문에 필요한 내용을 준비해 줘.",
        evidence_refs=[record.ref for record in records],
        capability_ids=[capability.capability_id],
        primary_group_hint="follow-ups",
        risk=Risk.LOW,
        requested_actions=[action],
    )
    return request, InMemoryContextRepository(
        evidence=records, capabilities=[capability]
    ).open_scope(request)


def ready(**changes):
    return {
        "status": "READY",
        "artifact_type": "CHECKLIST",
        "title": "Prepare application materials",
        "content": "- Complete the application using the required form\n- Prepare proof of enrollment\n- Submit the documents by 2030-05-01",
        "question": "",
        "explanation": "The checklist preserves the requested documents and deadline.",
        "source_ref": "m1",
        "support_quote_ref": "m1p1",
        "user_fact_ref": "",
        "validation_reason": "원문에서 요청한 자료와 기한을 보존했어요.",
        **changes,
    }


def no_action():
    return ready(
        status="NO_ACTION",
        artifact_type="NONE",
        title="",
        content="",
        explanation="The change applies automatically; no action is required.",
    )


def needs_input(**changes):
    return {
        **ready(
            status="NEEDS_INPUT",
            artifact_type="NONE",
            title="Confirm attendance",
            content="",
            question="Will you attend the seminar?",
            explanation="Your attendance decision is needed before the reply can be completed.",
        ),
        **changes,
    }


class SchemaModel(DeterministicModel):
    async def stream(self, messages, tool_specs=None, *args, **kwargs):
        self.schema = tool_specs[0]["inputSchema"]["json"]
        async for event in super().stream(messages, tool_specs, *args, **kwargs):
            yield event


class Factory:
    def __init__(self, draft=None, final=None):
        self.outputs = {
            "local_artifact_writer": ready() if draft is None else draft,
            "local_artifact_verifier": ready() if final is None else final,
        }
        self.models = {}

    def create(self, role, plan):
        payload = self.outputs[role]
        responses = payload if isinstance(payload, list) else [payload, payload]
        model = SchemaModel(
            role,
            ModelPlan(
                steps=tuple(
                    ToolStep("LocalPreparationAssessment", response)
                    for response in responses
                ),
                output=plan.output,
            ),
        )
        self.models[role] = model
        return model


def previous_result(**changes):
    return LocalPreparationResult(
        **{
            key: value
            for key, value in ready(source_ref="gmail:local-source", **changes).items()
            if key in LocalPreparationResult.model_fields
        }
    )


def test_refinement_receives_previous_draft_without_turning_it_into_evidence():
    request, scope = inputs(direct="두 번째 항목은 빼 줘.")
    previous = previous_result(
        title="신청 자료 준비",
        content="- 지정 양식으로 신청서 작성\n- 재학증명서 준비\n- 2030-05-01까지 서류 제출",
        explanation="이전에 준비한 한국어 체크리스트예요.",
    )
    original_history = previous.model_dump()
    edited = ready(
        content="- Complete the application using the required form\n- Submit the documents by 2030-05-01",
        explanation="The checklist omits the second item.",
    )
    factory = Factory(edited, edited)
    result = prepare_local_artifact(request, scope, factory, previous=previous)
    assert result.content == edited["content"]
    assert previous.model_dump() == original_history
    for model in factory.models.values():
        prompt = json.loads(model.received_messages[0][0]["content"][0]["text"])
        assert prompt["previous_preparation"]["content"] == previous.content
        assert prompt["previous_preparation"]["source_ref"] == "m1"
        assert [source["kind"] for source in prompt["sources"]] == ["gmail", "direct"]
        assert "gmail:local-source" not in json.dumps(prompt)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_ref": "gmail:other-source"},
        {"artifact_type": "REPLY_DRAFT"},
        {"content": ""},
        {"question": "다른 일을 할까요?"},
    ],
)
def test_previous_draft_must_match_the_selected_preparation(changes):
    request, scope = inputs(direct="두 번째 항목은 빼 줘.")
    previous = previous_result().model_copy(update=changes)
    factory = Factory()
    with pytest.raises(LocalPreparationSourceUnavailable):
        prepare_local_artifact(request, scope, factory, previous=previous)
    assert not factory.models


def test_previous_ai_reply_cannot_authorize_a_personal_commitment():
    request, scope = inputs(
        "prepare_reply",
        body="세미나 참석 여부를 회신해 주세요.",
        direct="문장을 더 짧게 해 줘.",
    )
    previous = previous_result(artifact_type="REPLY_DRAFT", content="I will attend.")
    invented = ready(
        artifact_type="REPLY_DRAFT",
        content="I will attend.",
        user_fact_ref="m2p1",
    )
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(
            request, scope, Factory(invented, invented), previous=previous
        )


def test_explicit_user_correction_can_replace_previous_no_action():
    request, scope = inputs(
        "prepare_reply",
        body="세미나 참석 여부를 회신해 주세요.",
        direct="참석하겠습니다. 참석한다고 답장 초안을 써 줘.",
    )
    previous = previous_result(
        status="NO_ACTION",
        artifact_type="NONE",
        title="",
        content="",
        explanation="별도 답장을 준비하지 않았어요.",
    )
    reply = ready(
        artifact_type="REPLY_DRAFT",
        title="Seminar attendance reply",
        content="Hello, I will attend the seminar. Thank you.",
        user_fact_ref="m2p1",
    )
    result = prepare_local_artifact(
        request, scope, Factory(reply, reply), previous=previous
    )
    assert result.status == "READY" and result.content == reply["content"]


def test_ready_checklist_uses_real_sdk_and_returns_only_clean_public_fields():
    request, scope = inputs()
    factory = Factory()
    result = prepare_local_artifact(request, scope, factory)
    assert result.status == "READY" and result.artifact_type == "CHECKLIST"
    assert (
        len(result.content.splitlines()) == 3
        and result.source_ref == "gmail:local-source"
    )
    assert set(result.model_dump()) == {
        "status",
        "artifact_type",
        "title",
        "content",
        "question",
        "explanation",
        "source_ref",
    }
    prompts = []
    for model in factory.models.values():
        assert model.stream_calls == 1 and model.tool_calls == [
            "LocalPreparationAssessment"
        ]
        assert set(model.schema["required"]) == set(ready())
        assert all(
            field["type"] == "string" for field in model.schema["properties"].values()
        )
        prompts.append(json.loads(model.received_messages[0][0]["content"][0]["text"]))
    assert list(factory.models) == ["local_artifact_writer", "local_artifact_verifier"]
    assert prompts[0]["sources"] == prompts[1]["sources"]
    assert "draft" in prompts[1] and "draft" not in prompts[0]
    assert BODY not in result.content


def test_reply_content_can_follow_the_original_language_without_inventing_a_promise():
    request, scope = inputs(
        "prepare_reply",
        "Please tell us which document format you need.",
        direct="PDF 양식으로 받고 싶다고 답장 초안을 작성해 줘.",
    )
    payload = ready(
        artifact_type="REPLY_DRAFT",
        title="Request the form",
        content="Hello, please share the PDF form. Thank you.",
        explanation="The reply requests the PDF form the user asked for.",
        user_fact_ref="m2p1",
    )
    result = prepare_local_artifact(request, scope, Factory(payload, payload))
    assert (
        result.content == payload["content"] and result.artifact_type == "REPLY_DRAFT"
    )


def test_reminder_is_useful_wording_without_scheduling_any_notification():
    request, scope = inputs(
        "prepare_reminder",
        "상담은 2030-05-01T10:00:00+09:00입니다. 신청서를 지참하세요.",
    )
    payload = ready(
        artifact_type="REMINDER",
        title="Appointment reminder text",
        content="Bring the application to the appointment at 2030-05-01T10:00:00+09:00.",
        explanation="The reminder preserves the appointment time and required document.",
    )
    result = prepare_local_artifact(request, scope, Factory(payload, payload))
    assert result.artifact_type == "REMINDER" and result.question == ""


@pytest.mark.parametrize(
    "source_date,display_date",
    [
        ("9월 14일", "September 14"),
        ("2030년 9월 14일", "September 14, 2030"),
        ("2030-09-14", "September 14, 2030"),
        ("2030년 9월 14일", "2030-09-14"),
    ],
)
def test_equivalent_source_dates_can_be_localized_in_prepared_content(
    source_date, display_date
):
    request, scope = inputs(
        "prepare_reminder",
        f"Encryption policy changes start on {source_date}. Review client compatibility.",
    )
    payload = ready(
        artifact_type="REMINDER",
        title="Encryption policy notice",
        content=f"Review client compatibility for the encryption policy change on {display_date}.",
        explanation="The reminder preserves the source date of the policy change.",
    )
    result = prepare_local_artifact(request, scope, Factory(payload, payload))
    assert result.content == payload["content"]


@pytest.mark.parametrize(
    "source_date,display_date",
    [
        ("September 14", "9월 15일"),
        ("September 14", "2030년 9월 14일"),
        ("2030-09-14", "2029년 9월 14일"),
        ("September 14 at 3PM PDT", "9월 14일 15:00 UTC"),
    ],
)
def test_localized_dates_cannot_change_source_facts_or_invent_year_and_time_zone(
    source_date, display_date
):
    request, scope = inputs(
        "prepare_reminder", f"Encryption policy changes start on {source_date}."
    )
    payload = ready(
        artifact_type="REMINDER",
        title="Encryption policy notice",
        content=f"Review the encryption policy change on {display_date}.",
        explanation="The reminder describes the policy change.",
    )
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(payload, payload))


@pytest.mark.parametrize(
    "source_clock,display_clock",
    [
        ("오후 8시", "8PM"),
        ("오후 8시 30분", "8:30 PM"),
        ("오전 12시", "12AM"),
        ("12PM", "12:00"),
        ("20:00", "8PM"),
    ],
)
def test_source_clock_notation_can_be_localized_without_changing_time_or_zone(
    source_clock, display_clock
):
    request, scope = inputs(
        "prepare_reminder", f"Submission closes September 14 at {source_clock} EDT."
    )
    payload = ready(
        artifact_type="REMINDER",
        title="Submission deadline",
        content=f"Complete the submission by September 14 at {display_clock} EDT.",
        explanation="The reminder preserves the source deadline and time zone.",
    )
    assert (
        prepare_local_artifact(request, scope, Factory(payload, payload)).content
        == payload["content"]
    )


@pytest.mark.parametrize(
    "source_text,content",
    [
        (
            "Submission closes September 14 at 8PM EDT.",
            "9월 14일 9PM EDT까지 제출해 주세요.",
        ),
        (
            "Submission closes September 14 at 8PM EDT.",
            "9월 14일 8PM까지 제출해 주세요.",
        ),
        (
            "Submission closes September 14 at 8PM EDT.",
            "9월 14일 오후 8시 KST까지 제출해 주세요.",
        ),
        (
            "Submission closes September 14. Source time zone: UTC-04:00.",
            "9월 14일 04:00 UTC-04:00까지 제출해 주세요.",
        ),
        (
            "상담은 2030-09-14T10:00:00+09:00입니다.",
            "2031-09-14T10:00:00+09:00에 참석하세요.",
        ),
        (
            "상담은 2030-09-14T10:00:00+09:00입니다.",
            "2030-09-14T10:00:00+08:00에 참석하세요.",
        ),
        ("상담은 2030-09-14T10:00:00+09:00입니다.", "10:00에 참석하세요."),
        ("자세한 내용은 https://example.test/03:00 에 있어요.", "03:00에 제출하세요."),
    ],
)
def test_clock_copy_rejects_changed_time_missing_zone_and_offset_as_event_clock(
    source_text, content
):
    request, scope = inputs("prepare_reminder", source_text)
    payload = ready(
        artifact_type="REMINDER",
        title="일정 알림",
        content=content,
        explanation="일정 정보를 정리했어요.",
    )
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(payload, payload))


def test_verifier_rejects_gratuitous_work_and_explains_no_action():
    request, scope = inputs(
        body="변경 사항은 자동으로 적용됩니다. 별도 조치가 필요하지 않습니다."
    )
    wrong = ready(content="- 안내를 확인하세요.")
    result = prepare_local_artifact(request, scope, Factory(wrong, no_action()))
    assert result.status == "NO_ACTION" and result.artifact_type == "NONE"
    assert (
        result.explanation
        and not result.title
        and not result.content
        and not result.question
    )
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(wrong, wrong))


def test_unknown_attendance_becomes_one_user_decision_not_a_fabricated_reply():
    request, scope = inputs("prepare_reply", "다음 세미나 참석 여부를 회신해 주세요.")
    invented = ready(
        artifact_type="REPLY_DRAFT",
        content="Hello, I will attend the seminar. Thank you.",
    )
    result = prepare_local_artifact(request, scope, Factory(invented, needs_input()))
    assert (
        result.status == "NEEDS_INPUT"
        and result.question.count("?") == 1
        and result.content == ""
    )
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(invented, invented))


def test_user_confirmed_attendance_can_be_written_into_a_reply():
    request, scope = inputs(
        "prepare_reply",
        "다음 세미나 참석 여부를 회신해 주세요.",
        direct="참석하겠습니다. 그렇게 회신 문안을 작성해 줘.",
    )
    payload = ready(
        artifact_type="REPLY_DRAFT",
        content="Hello, I will attend the seminar. Thank you.",
        explanation="The reply reflects the attendance decision the user provided.",
        user_fact_ref="m2p1",
    )
    assert (
        prepare_local_artifact(request, scope, Factory(payload, payload)).status
        == "READY"
    )


def test_an_uncertain_direct_statement_is_not_proof_of_attendance():
    request, scope = inputs(
        "prepare_reply",
        "다음 세미나 참석 여부를 회신해 주세요.",
        direct="참석할지 아직 결정하지 못했어요.",
    )
    payload = ready(
        artifact_type="REPLY_DRAFT",
        content="I will attend the seminar.",
        user_fact_ref="m2p1",
    )
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(payload, payload))


@pytest.mark.parametrize(
    "direct",
    [
        "참석하겠습니다라고 답장하지 마세요.",
        "만약 시간이 되면 I will attend 라고 답할 수도 있어요.",
        "참석하겠습니다. 불참한다고도 적어 주세요.",
    ],
)
def test_negated_conditional_or_conflicting_user_words_do_not_authorize_a_commitment(
    direct,
):
    request, scope = inputs(
        "prepare_reply", "세미나 참석 여부를 회신해 주세요.", direct=direct
    )
    payload = ready(
        artifact_type="REPLY_DRAFT",
        content="I will attend the seminar.",
        user_fact_ref="m2p1",
    )
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(payload, payload))


def test_unknown_account_activity_requires_the_users_knowledge_before_next_steps():
    request, scope = inputs(
        body="이 로그인이 본인의 활동인지 확인하세요. 본인이라면 별도 조치가 필요하지 않습니다."
    )
    question = needs_input(
        title="Review sign-in",
        question="Was this sign-in yours?",
        explanation="The next step depends on whether the sign-in was yours.",
    )
    result = prepare_local_artifact(
        request, scope, Factory(ready(content="- 계정 비밀번호 변경"), question)
    )
    assert result.status == "NEEDS_INPUT" and result.content == ""


@pytest.mark.parametrize(
    "changes",
    [
        {"content": "- 안내를 확인하세요."},
        {"content": "- 2031-05-01까지 서류 제출"},
        {"content": "- 내일까지 서류 제출"},
        {"content": "Prepare application materials"},
        {"content": "신청서와 재학증명서 준비"},
        {"content": "- m1p1의 정보를 확인하세요."},
        {"explanation": "메일을 발송했습니다."},
        {"explanation": "알림을 등록했어요."},
        {"artifact_type": "REMINDER"},
    ],
)
def test_invalid_or_useless_final_content_cannot_become_ready(changes):
    request, scope = inputs()
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(ready(), ready(**changes)))


@pytest.mark.parametrize(
    "content",
    [
        "파일을 첨부했습니다.",
        "신청서를 제출했습니다.",
        "I will send the report tomorrow.",
        "Please find attached the form.",
    ],
)
def test_reply_never_claims_unperformed_attachments_completion_or_promises(content):
    request, scope = inputs("prepare_reply")
    payload = ready(artifact_type="REPLY_DRAFT", content=content)
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(payload, payload))


@pytest.mark.parametrize(
    "changes",
    [
        {"question": "참석할까요? 자료도 보낼까요?"},
        {"question": "회신 문안을 준비해도 될까요?"},
        {"content": "일단 참석한다고 적었어요."},
        {"explanation": ""},
    ],
)
def test_needs_input_has_one_essential_question_and_an_explanation(changes):
    request, scope = inputs("prepare_reply", "세미나 참석 여부를 회신해 주세요.")
    payload = needs_input(**changes)
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(payload, payload))


def test_repair_occurs_within_the_existing_sdk_budget_and_failure_is_not_no_action():
    request, scope = inputs()
    factory = Factory(ready(), [ready(content="- 안내를 확인하세요."), ready()])
    assert prepare_local_artifact(request, scope, factory).status == "READY"
    assert factory.models["local_artifact_verifier"].stream_calls == 2
    bad = ready(content="- 안내를 확인하세요.")
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(bad, bad))


def test_sources_and_generated_output_are_masked_again_without_logging_secrets(capsys):
    secret = "PRIVATE-SYNTHETIC-PASSWORD"
    email, phone = "private-person@example.test", "010-1234-5678"
    request, scope = inputs(
        body=BODY + f"\nPassword: {secret}\nContact: {email} {phone}"
    )
    payload = ready(
        content=ready()["content"]
        + f"\n- Contact: {email} {phone}\n- Password: {secret}"
    )
    factory = Factory(payload, payload)
    result = prepare_local_artifact(request, scope, factory)
    serialized = json.dumps(result.model_dump(), ensure_ascii=False)
    messages = json.dumps(
        [model.received_messages for model in factory.models.values()],
        ensure_ascii=False,
    )
    logs = capsys.readouterr().out
    for value in (secret, email, phone):
        assert value not in serialized + messages + logs


def test_nested_entity_cannot_reveal_a_technical_id_after_validation(capsys):
    request, scope = inputs()
    encoded = ready(content="- 참고 m&amp;#49;p1의 일정 준비")
    factory = Factory(encoded, encoded)
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, factory)
    assert list(factory.models) == ["local_artifact_writer"]
    assert factory.models["local_artifact_writer"].stream_calls == 2
    assert "m&amp;#49;p1" not in capsys.readouterr().out


def test_nested_entities_cannot_restore_a_raw_mail_body_in_returned_content(capsys):
    body = "신청서에는 이름과 제출 항목을 적으세요. 지정 양식은 안내 페이지에서 받을 수 있습니다."
    request, scope = inputs("prepare_reply", body)
    encoded = "".join(f"&amp;#{ord(character)};" for character in body)
    payload = ready(
        artifact_type="REPLY_DRAFT",
        title="Application reply",
        content=encoded,
        explanation="Prepared a reply about the application instructions.",
    )
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(payload, payload))
    assert body not in capsys.readouterr().out


@pytest.mark.parametrize("field", ["title", "content", "question", "explanation"])
def test_nested_entities_are_rejected_in_every_display_field(field):
    request, scope = inputs()
    payload = (
        needs_input(question="m&amp;#49;p1의 날짜는 언제인가요?")
        if field == "question"
        else ready(
            **{field: "- 참고 m&amp;#49;p1의 자료 준비"}
            if field == "content"
            else {field: "m&amp;#49;p1의 자료를 준비해요."}
        )
    )
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(payload, payload))


@pytest.mark.parametrize("depth", [1, 2, 16, 128])
def test_deep_encoding_is_rejected_as_unstable_without_a_decoding_iteration_cap(depth):
    request, scope = inputs()
    pointer = "m&" + "amp;" * depth + "#49;p1"
    payload = ready(content=f"- 참고 {pointer}의 자료 준비")
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(payload, payload))


def test_unstable_encoding_can_be_repaired_to_plain_text_within_the_same_budget():
    request, scope = inputs()
    unstable = ready(content="- 신청서를 &amp;#51089;성하세요.")
    factory = Factory([unstable, ready()], ready())
    result = prepare_local_artifact(request, scope, factory)
    assert result.content == ready()["content"]
    assert factory.models["local_artifact_writer"].stream_calls == 2
    assert factory.models["local_artifact_verifier"].stream_calls == 1


def test_final_display_is_exactly_the_canonical_copy_verified_by_the_sdk():
    request, scope = inputs()
    payload = ready(title="Prepare application &amp; certificate")
    result = prepare_local_artifact(request, scope, Factory(payload, payload))
    assert result.title == "Prepare application & certificate"
    assert result.content == payload["content"]
    assert result.question == payload["question"]
    assert result.explanation == payload["explanation"]


@pytest.mark.parametrize("field", list(ready()))
def test_all_model_fields_are_required_and_missing_values_fail_closed(field):
    request, scope = inputs()
    payload = ready()
    del payload[field]
    with pytest.raises(LocalPreparationOutputError):
        prepare_local_artifact(request, scope, Factory(payload, payload))


def test_owned_source_refs_and_direct_user_facts_cannot_be_substituted():
    request, scope = inputs()
    for changes in (
        {"source_ref": "m2"},
        {"support_quote_ref": "m2p1"},
        {"user_fact_ref": "m1p1"},
    ):
        with pytest.raises(LocalPreparationOutputError):
            prepare_local_artifact(
                request, scope, Factory(ready(**changes), ready(**changes))
            )
    with pytest.raises(ContextAccessDenied):
        prepare_local_artifact(
            request.model_copy(update={"user_id": "other-owner"}), scope, Factory()
        )


@pytest.mark.parametrize(
    "body,facts",
    [
        (None, []),
        ("", []),
        (BODY, ["source_content=snippet_only"]),
        (BODY, ["source_truncated=true"]),
    ],
)
def test_missing_fresh_source_is_not_reported_as_no_action(body, facts):
    request, scope = inputs(body=body, facts=facts)
    factory = Factory()
    with pytest.raises(LocalPreparationSourceUnavailable):
        prepare_local_artifact(request, scope, factory)
    assert not factory.models
