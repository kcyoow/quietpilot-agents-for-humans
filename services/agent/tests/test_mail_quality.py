from __future__ import annotations

import json
import runpy
import unicodedata
from pathlib import Path

import pytest
from quietpilot_agent.local_model import DeterministicModel, ModelPlan, ToolStep
from quietpilot_agent.mail_interests import (
    MailDecision,
    MailDecisionAssessment,
    MailInterestOutputError,
    MailInterestProfile,
    match_interest_mail,
)
from quietpilot_agent.models import EvidenceRecord

FixtureMailModel = runpy.run_path(
    str(Path(__file__).with_name("mail_scripted_model.py"))
)["FixtureMailModel"]


def record(
    ref="gmail:owned",
    text="A third-party application was authorized with read-only access.",
):
    return EvidenceRecord(
        user_id="private-owner",
        ref=ref,
        revision=1,
        source="gmail",
        title="계정과 서비스 안내",
        facts=["sender_domain=service.test"],
        untrusted_text=text,
    )


def decision(
    ref="m1",
    *,
    tags=None,
    importance="LOW",
    description=False,
    excluded=False,
    quote="",
    summary="",
):
    return MailDecision(
        evidence_ref=ref,
        matched_tags=tags or [],
        description_match=description,
        excluded=excluded,
        importance=importance,
        summary=summary,
        reason="The source describes a change that needs review." if summary else "",
        supporting_quotes=[quote] if quote else [],
    )


def assessment(*decisions):
    return MailDecisionAssessment(
        assessed_evidence_refs=[d.evidence_ref for d in decisions],
        decisions=list(decisions),
    )


class Factory:
    def __init__(self, draft, reviewed):
        self.outputs = {
            "mail_interest_matcher": draft,
            "mail_interest_verifier": reviewed,
        }
        self.models = {}

    def create(self, role, plan):
        output = self.outputs[role]
        if isinstance(output, dict):
            step = ToolStep("MailFieldAssessment", output)
            model = FieldModel(role, ModelPlan(steps=(step,) * 4, output=plan.output))
        else:
            model = FixtureMailModel(role, ModelPlan(steps=(), output=output))
        self.models[role] = model
        return model


@pytest.mark.parametrize(
    ("role", "source_ref"),
    [
        ("mail_interest_matcher", "        "),
        ("mail_interest_matcher", "m1p99"),
        ("mail_interest_verifier", "        "),
        ("mail_interest_verifier", "m1p99"),
        ("mail_interest_verifier", ""),
    ],
)
def test_selected_mail_requires_a_valid_source_reference_in_each_pass(role, source_ref):
    source = record()
    valid = {
        **negative_fields(),
        "m1_importance": "HIGH",
        "m1_summary": "Review the external application’s read-only access.",
        "m1_reason": "Account access permissions changed.",
        "m1_source_ref": "m1p1",
    }
    invalid = {**valid, "m1_source_ref": source_ref}
    factory = Factory(
        invalid if role == "mail_interest_matcher" else valid,
        invalid if role == "mail_interest_verifier" else valid,
    )
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]), [source], factory
        )
    assert factory.models[role].stream_calls == 4


def test_negative_decisions_do_not_need_invented_tags_and_reviewer_can_reject_draft():
    source = record(text="Discover recommended social accounts in your feed.")
    wrong = decision(
        tags=["보안알림"],
        importance="NORMAL",
        quote=source.untrusted_text,
        summary="Introduces recommended accounts.",
    )
    factory = Factory(assessment(wrong), assessment(decision()))
    assert (
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]), [source], factory
        )
        == []
    )
    assert list(factory.models) == ["mail_interest_matcher", "mail_interest_verifier"]
    assert all(m.stream_calls == 1 for m in factory.models.values())
    assert all(
        m.seen_tool_names == [frozenset({"MailFieldAssessment"})]
        for m in factory.models.values()
    )


def test_reviewer_recovers_personal_deadline_outside_tags_without_fabricating_tag():
    source = record(
        text="You registered for this event. Submission deadline: 2026-10-03 23:59 KST."
    )
    final = decision(
        importance="HIGH",
        quote=source.untrusted_text,
        summary="The registered event’s submission deadline is 2026-10-03 at 23:59 KST.",
    )
    factory = Factory(assessment(decision()), assessment(final))
    matches = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]), [source], factory
    )
    assert matches[0].evidence_ref == source.ref and matches[0].matched_tags == []
    assert matches[0].importance == "HIGH"
    assert "supporting_quotes" not in matches[0].model_dump()
    for model in factory.models.values():
        messages = json.dumps(model.received_messages)
        assert source.ref not in messages and source.user_id not in messages


def test_low_priority_is_not_selected_even_with_a_real_interest_tag():
    source = record(
        text="Use this one-time verification code for your current sign-in."
    )
    low = decision(tags=["보안알림"], importance="LOW")
    assert (
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]),
            [source],
            Factory(assessment(low), assessment(low)),
        )
        == []
    )


def test_normal_informational_dataset_mail_remains_useful_without_an_action():
    source = record(
        text="A Parquet version of this dataset is now available. No action is required."
    )
    keep = decision(
        tags=["데이터셋"],
        importance="NORMAL",
        quote=source.untrusted_text,
        summary="A converted dataset is available. No action is required.",
    )
    matches = match_interest_mail(
        MailInterestProfile(revision=1, tags=["데이터셋"]),
        [source],
        Factory(assessment(keep), assessment(keep)),
    )
    assert len(matches) == 1 and matches[0].importance == "NORMAL"


def test_reviewed_copy_preserves_forecast_and_external_party_qualifiers():
    source = record(
        text="FORECASTED cost is $2.40 against a $2.00 budget. A third-party app was authorized."
    )
    draft = decision(
        tags=["보안알림"],
        importance="HIGH",
        quote=source.untrusted_text,
        summary="Costs exceeded the budget and a third application was added.",
    )
    reviewed = decision(
        tags=["보안알림"],
        importance="HIGH",
        quote=source.untrusted_text,
        summary="Forecast cost is $2.40 against a $2.00 budget. An external app was also authorized.",
    )
    matches = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]),
        [source],
        Factory(assessment(draft), assessment(reviewed)),
    )
    assert (
        matches[0].summary == reviewed.summary and matches[0].summary != draft.summary
    )


@pytest.mark.parametrize(
    "bad",
    [
        decision(
            importance="HIGH",
            quote="This unsupported event never appeared in the email.",
            summary="새로운 사건이 있어요.",
        ),
        decision(importance="HIGH", summary="변경이 있어요."),
        decision(tags=["임의관심"], importance="NORMAL"),
        decision(description=True, importance="NORMAL"),
    ],
)
def test_invalid_review_is_terminal_without_empty_success(bad):
    source = record()
    factory = Factory(assessment(decision()), assessment(bad))
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]), [source], factory
        )
    assert factory.models["mail_interest_verifier"].stream_calls == 4


def test_missing_negative_decision_is_not_silently_treated_as_assessed(capsys):
    invalid = MailDecisionAssessment(
        assessed_evidence_refs=["m1", "m2"], decisions=[decision()]
    )
    factory = Factory(invalid, assessment(decision()))
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]),
            [record(), record("gmail:other")],
            factory,
        )
    assert set(factory.models) == {"mail_interest_matcher"}
    diagnostic = json.loads(capsys.readouterr().out)
    expected_fields = {f"m2_{suffix}" for suffix in FIELD_SUFFIXES}
    assert all(
        all(shape[field] == "missing" for field in expected_fields)
        for shape in diagnostic["tool_input_shapes"]
    )
    assert {tuple(error["loc"]) for error in diagnostic["tool_validation_errors"]} == {
        (name,) for name in expected_fields
    }
    assert all(
        error["type"] == "missing" for error in diagnostic["tool_validation_errors"]
    )


def test_description_only_matches_do_not_need_synthetic_tags():
    source = record(
        text="The library is opening a new study room for enrolled students."
    )
    keep = decision(
        description=True,
        importance="NORMAL",
        quote=source.untrusted_text,
        summary="A new study space is opening in the school library.",
    )
    matches = match_interest_mail(
        MailInterestProfile(revision=1, description="학교 도서관 소식"),
        [source],
        Factory(assessment(keep), assessment(keep)),
    )
    assert matches[0].matched_tags == []


def test_foreign_decision_cannot_borrow_an_owned_reference_with_a_valid_quote():
    source = record()
    foreign = decision(
        "foreign",
        importance="HIGH",
        quote=source.untrusted_text,
        summary="Review external app access to the account.",
    )
    invalid = MailDecisionAssessment(assessed_evidence_refs=["m1"], decisions=[foreign])
    factory = Factory(invalid, invalid)
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]), [source], factory
        )
    assert factory.models["mail_interest_matcher"].stream_calls == 4


FIELD_SUFFIXES = [
    "tag_refs",
    "description_match",
    "excluded",
    "importance",
    "summary",
    "reason",
    "source_ref",
]


class FieldModel(DeterministicModel):
    async def stream(self, messages, tool_specs=None, *args, **kwargs):
        self.schema = tool_specs[0]["inputSchema"]["json"]
        async for event in super().stream(messages, tool_specs, *args, **kwargs):
            yield event


class FieldFactory(Factory):
    def __init__(self, payload):
        super().__init__(payload, payload)


def negative_fields(ref="m1"):
    values = ["", "NO", "NO", "LOW", "", "", ""]
    return {
        f"{ref}_{key}": value for key, value in zip(FIELD_SUFFIXES, values, strict=True)
    }


@pytest.mark.parametrize("suffix", FIELD_SUFFIXES)
@pytest.mark.parametrize("corruption", ["missing", "array", "object"])
def test_real_sdk_rejects_missing_mail_fields_and_non_scalar_values(suffix, corruption):
    payload = negative_fields()
    if corruption == "missing":
        del payload[f"m1_{suffix}"]
    else:
        payload[f"m1_{suffix}"] = [] if corruption == "array" else {}
    factory = FieldFactory(payload)
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]), [record()], factory
        )
    assert set(factory.models) == {"mail_interest_matcher"}
    assert factory.models["mail_interest_matcher"].stream_calls == 4


@pytest.mark.parametrize("tag_ids", ["t9", "t1,t1", "t1,", "보안알림"])
def test_real_sdk_rejects_unknown_duplicate_or_empty_tag_ids(tag_ids):
    factory = FieldFactory({**negative_fields(), "m1_tag_refs": tag_ids})
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]), [record()], factory
        )
    assert factory.models["mail_interest_matcher"].stream_calls == 4


def test_real_sdk_scalar_fields_bind_each_summary_and_source_to_its_own_mail():
    first = record("gmail:first", text="A new external application has account access.")
    second = record("gmail:second", text="The dataset Parquet export is now ready.")
    payload = {
        **negative_fields("m1"),
        **negative_fields("m2"),
        "m1_tag_refs": "t1",
        "m1_importance": "HIGH",
        "m1_summary": "Review external app access to the account.",
        "m1_reason": "Account access permissions changed.",
        "m1_source_ref": "m1p1",
        "m2_tag_refs": "t2",
        "m2_importance": "NORMAL",
        "m2_summary": "Dataset conversion is complete.",
        "m2_reason": "Matches the saved dataset topic.",
        "m2_source_ref": "m2p1",
    }
    payload = dict(reversed(list(payload.items())))
    profile = MailInterestProfile(revision=1, tags=["보안알림", "데이터셋"])
    factory = FieldFactory(payload)
    matches = match_interest_mail(profile, [first, second], factory)
    assert [(m.evidence_ref, m.matched_tags, m.importance) for m in matches] == [
        (first.ref, ["보안알림"], "HIGH"),
        (second.ref, ["데이터셋"], "NORMAL"),
    ]
    for model in factory.models.values():
        schema = model.schema
        expected = {f"m{i}_{suffix}" for i in (1, 2) for suffix in FIELD_SUFFIXES}
        assert set(schema["required"]) == set(schema["properties"]) == expected
        # Strands omits additionalProperties in its tool spec; the actual SDK
        # extra-field rejection is covered by the extra_row regression.
        assert all(field["type"] == "string" for field in schema["properties"].values())
        for i in (1, 2):
            assert schema["properties"][f"m{i}_description_match"]["const"] == "NO"
            assert schema["properties"][f"m{i}_tag_refs"]["enum"] == [
                "",
                "t1",
                "t2",
                "t1,t2",
            ]
            assert schema["properties"][f"m{i}_source_ref"]["enum"] == ["", f"m{i}p1"]
        prompt = json.loads(model.received_messages[0][0]["content"][0]["text"])
        assert prompt["tag_dictionary"] == {"t1": "보안알림", "t2": "데이터셋"}
        assert all("untrusted_text" not in row for row in prompt["evidence"])
        assert prompt["evidence"][0]["source_passages"]["m1p1"].endswith(
            first.untrusted_text
        )
    wrong = FieldFactory({**payload, "m1_source_ref": "m2p1", "m2_source_ref": "m1p1"})
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(profile, [first, second], wrong)


@pytest.mark.parametrize(
    "body", ["x" * 4000, "word " * 799 + "end", "x" * 475 + " tail", "가 " * 900]
)
def test_passage_prompt_preserves_the_complete_bounded_source_in_both_passes(body):
    source = record(text=body)
    factory = FieldFactory(negative_fields())
    assert (
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["데이터셋"]), [source], factory
        )
        == []
    )
    prompts = []
    for model in factory.models.values():
        prompt = json.loads(model.received_messages[0][0]["content"][0]["text"])
        prompts.append(prompt)
        passages = prompt["evidence"][0]["source_passages"]
        normalized = " ".join(
            unicodedata.normalize("NFC", source.title + " " + body).split()
        )
        assert "".join(passages.values()) == normalized
        assert all(
            8 <= len(" ".join(text.split())) <= len(text) <= 480
            for text in passages.values()
        )
        assert list(passages) == [f"m1p{i}" for i in range(1, len(passages) + 1)]
        assert model.schema["properties"]["m1_source_ref"]["enum"] == ["", *passages]
    assert prompts[0]["evidence"] == prompts[1]["evidence"]


def test_short_source_is_visible_but_not_fabricated_into_eligible_grounding():
    source = record(text="").model_copy(update={"title": "알림"})
    factory = FieldFactory(negative_fields())
    assert (
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]), [source], factory
        )
        == []
    )
    model = factory.models["mail_interest_matcher"]
    prompt = json.loads(model.received_messages[0][0]["content"][0]["text"])
    assert "".join(prompt["evidence"][0]["source_passages"].values()) == "알림"
    assert model.schema["properties"]["m1_source_ref"]["const"] == ""


def test_tag_combinations_cover_all_unique_subsets_within_the_eight_tag_bound():
    factory = FieldFactory(negative_fields())
    profile = MailInterestProfile(revision=1, tags=[f"주제{i}" for i in range(8)])
    assert match_interest_mail(profile, [record()], factory) == []
    options = factory.models["mail_interest_matcher"].schema["properties"][
        "m1_tag_refs"
    ]["enum"]
    assert len(options) == len(set(options)) == 256
    assert "" in options and "t1,t2,t3,t4,t5,t6,t7,t8" in options
    assert all(len(value) <= 23 for value in options)
    assert all(len(value.split(",")) == len(set(value.split(","))) for value in options)


def test_real_sdk_repairs_language_with_precise_field_feedback_and_preserves_grounding():
    valid = {
        **negative_fields(),
        "m1_importance": "HIGH",
        "m1_summary": "An external app received read-only access.",
        "m1_reason": "Account permissions changed and need review.",
        "m1_source_ref": "m1p1",
    }
    invalid = {**valid, "m1_summary": "검토가 필요한 계정 권한 변경이에요."}

    class RepairFactory(FieldFactory):
        def create(self, role, plan):
            model = super().create(role, plan)
            if role == "mail_interest_verifier":
                model.plan = ModelPlan(
                    steps=(
                        ToolStep("MailFieldAssessment", invalid),
                        ToolStep("MailFieldAssessment", valid),
                    ),
                    output=plan.output,
                )
            return model

    factory = RepairFactory(valid)
    result = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]), [record()], factory
    )
    assert result[0].summary == valid["m1_summary"]
    reviewer = factory.models["mail_interest_verifier"]
    assert (
        reviewer.stream_calls == 2
        and factory.models["mail_interest_matcher"].stream_calls == 1
    )
    feedback = [
        item["text"]
        for message in reviewer.received_messages[1]
        for block in message.get("content", [])
        for item in block.get("toolResult", {}).get("content", [])
        if "text" in item
    ]
    assert any(
        "m1_summary" in text and "concise English prose" in text for text in feedback
    )


def test_source_time_quote_with_internal_period_survives_both_real_sdk_passes():
    source = record(
        text="You signed up for Future Agents Hackathon. Only 3 days left. Submit your project."
    ).model_copy(update={"title": "Final Call: Future Agents Hackathon"})
    valid = {
        **negative_fields(),
        "m1_importance": "HIGH",
        "m1_summary": "This submission reminder said '3 days left.' when the email was sent.",
        "m1_reason": "A submission reminder for an event you registered for.",
        "m1_source_ref": "m1p1",
    }
    factory = Factory(valid, valid)
    result = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]), [source], factory
    )
    assert result[0].summary == valid["m1_summary"]
    assert all(model.stream_calls == 1 for model in factory.models.values())


@pytest.mark.parametrize(
    "summary",
    [
        "This submission reminder said '4 days left.' when the email was sent.",
        "This submission reminder says '3 days left.'.",
        "When the email was sent, it said '3 days left.'. The deadline has 2 days remaining.",
        "This submission reminder said '3 days left.' when the email was sent; 2 days remain now.",
        "This submission reminder said '3 days left.' when the email was sent, but currently 2 days remain.",
    ],
)
def test_quoted_period_does_not_hide_wrong_source_or_missing_current_time_basis(
    summary, capsys
):
    source = record(
        text="You signed up for Future Agents Hackathon. Only 3 days left. Submit your project."
    ).model_copy(update={"title": "Final Call: Future Agents Hackathon"})
    fields = {
        **negative_fields(),
        "m1_importance": "HIGH",
        "m1_summary": summary,
        "m1_reason": "A submission reminder for an event you registered for.",
        "m1_source_ref": "m1p1",
    }
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]),
            [source],
            Factory(fields, fields),
        )
    assert (
        "source_relative_day_basis"
        in json.loads(capsys.readouterr().out)["rejected_rules"]
    )


@pytest.mark.parametrize(
    "summary",
    [
        "등록한 행사의 제출 기한까지 3일이 남았음을 알려주는 최종 알림입니다.",
        "등록한 행사의 제출 마감까지 남은 기간은 3일입니다.",
        "The registered event's submission deadline has 3 days remaining.",
    ],
)
def test_real_sdk_repairs_unanchored_relative_countdown_with_korean_particle(summary):
    source = record(
        text="You signed up for Example Hackathon. There are only 3 days left to submit your project."
    ).model_copy(update={"title": "Final call for submissions - Example Hackathon"})
    valid = {
        **negative_fields(),
        "m1_importance": "HIGH",
        "m1_summary": "This final submission reminder said '3 days left' when the email was sent.",
        "m1_reason": "Describes the submission deadline for the registered event.",
        "m1_source_ref": "m1p1",
    }
    invalid = {**valid, "m1_summary": summary}

    class RepairFactory(FieldFactory):
        def create(self, selected_role, plan):
            model = super().create(selected_role, plan)
            if selected_role == "mail_interest_verifier":
                model.plan = ModelPlan(
                    steps=(
                        ToolStep("MailFieldAssessment", invalid),
                        ToolStep("MailFieldAssessment", valid),
                    ),
                    output=plan.output,
                )
            return model

    factory = RepairFactory(valid)
    matches = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]), [source], factory
    )
    assert matches[0].summary == valid["m1_summary"]
    repaired = factory.models["mail_interest_verifier"]
    assert repaired.stream_calls == 2
    example = repaired.schema["properties"]["m1_summary"]["examples"][0]
    assert "'3 days left' when the email was sent" in example
    assert (
        matches[0].summary != example
    )  # The example does not replace valid model copy.
    assert factory.models["mail_interest_matcher"].stream_calls == 1
    feedback = [
        item["text"]
        for message in repaired.received_messages[1]
        for block in message.get("content", [])
        for item in block.get("toolResult", {}).get("content", [])
        if "text" in item
    ]
    assert any(
        "m1_summary" in text and "current countdown" in text for text in feedback
    )
    assert any(
        "m1_summary" in text
        and "verbatim source quote in its original language" in text
        and "when the email was sent" in text
        and "omit an optional relative countdown" in text
        and "retain any stated absolute deadline and all mandatory source facts" in text
        for text in feedback
    )


def test_persistent_language_failure_reports_field_without_generated_copy(capsys):
    private = "PRIVATE-SYNTHETIC-PROSE"
    payload = {
        **negative_fields(),
        "m1_importance": "HIGH",
        "m1_summary": "계정 권한을 확인하세요. " + private,
        "m1_reason": "Review account access permissions.",
        "m1_source_ref": "m1p1",
    }
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]),
            [record()],
            FieldFactory(payload),
        )
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.out)
    assert diagnostic["rejected_rules"] == ["copy_language"]
    assert (
        diagnostic["tool_validation_errors"]
        == [{"loc": ["m1_summary"], "type": "value_error"}] * 4
    )
    assert private not in captured.out + captured.err


def test_source_fact_diagnostics_identify_rules_without_source_values(capsys):
    source = record(
        text="삭제예정일: 2030-10-07. 개인정보와 잔액은 삭제되며 복구할 수 없습니다. PRIVATE-SOURCE-MARKER"
    ).model_copy(update={"title": "계정 삭제 예정 안내"})
    payload = {
        **negative_fields(),
        "m1_importance": "HIGH",
        "m1_summary": "The account is scheduled for deletion and needs review.",
        "m1_reason": "Review the account notice.",
        "m1_source_ref": "m1p1",
    }
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]),
            [source],
            FieldFactory(payload),
        )
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.out)
    assert {
        "source_facts",
        "source_deletion_date",
        "source_personal_loss",
        "source_balance_loss",
        "source_irreversible_loss",
    }.issubset(diagnostic["rejected_rules"])
    assert all(
        item["loc"] == ["m1_summary"] for item in diagnostic["tool_validation_errors"]
    )
    assert "PRIVATE" not in captured.out + captured.err
    assert "2030-10-07" not in captured.out + captured.err


def test_failed_relative_copy_can_omit_countdown_and_preserve_explicit_deadline():
    source = record(
        text="You signed up for Example Hackathon. Submission deadline: 2030-10-03 at 23:59 KST. Only 3 days left."
    ).model_copy(update={"title": "Final call for submissions - Example Hackathon"})
    valid = {
        **negative_fields(),
        "m1_importance": "HIGH",
        "m1_summary": "The registered event's submission deadline is 2030-10-03 at 23:59 KST.",
        "m1_reason": "Review the deadline for the event you registered for.",
        "m1_source_ref": "m1p1",
    }
    invalid = {**valid, "m1_summary": "The submission deadline has 3 days remaining."}

    class RepairFactory(FieldFactory):
        def create(self, role, plan):
            model = super().create(role, plan)
            if role == "mail_interest_verifier":
                model.plan = ModelPlan(
                    steps=(
                        ToolStep("MailFieldAssessment", invalid),
                        ToolStep("MailFieldAssessment", valid),
                    ),
                    output=plan.output,
                )
            return model

    factory = RepairFactory(valid)
    result = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]), [source], factory
    )
    reviewer = factory.models["mail_interest_verifier"]
    assert reviewer.stream_calls == 2
    assert "examples" not in reviewer.schema["properties"]["m1_summary"]
    assert result[0].summary == valid["m1_summary"]
    assert "omit an optional relative countdown" in json.dumps(
        reviewer.received_messages[1]
    )


def test_korean_internal_draft_reaches_independent_review_but_only_english_copy_is_returned():
    draft = {
        **negative_fields(),
        "m1_importance": "HIGH",
        "m1_summary": "외부 앱이 계정에 접근할 수 있어요.",
        "m1_reason": "계정 권한을 확인해 주세요.",
        "m1_source_ref": "m1p1",
    }
    reviewed = {
        **draft,
        "m1_summary": "An external app received account access.",
        "m1_reason": "Review the changed account permissions.",
    }
    factory = Factory(draft, reviewed)
    result = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]), [record()], factory
    )
    assert result[0].summary == reviewed["m1_summary"]
    assert all(model.stream_calls == 1 for model in factory.models.values())
    reviewer = factory.models["mail_interest_verifier"]
    prompt = json.loads(reviewer.received_messages[0][0]["content"][0]["text"])
    assert prompt["draft"] == draft
    assert prompt["evidence"][0]["source_passages"]["m1p1"].endswith(
        record().untrusted_text
    )


def test_real_sdk_can_finish_all_validation_layers_within_four_mail_attempts(capsys):
    valid = {
        **negative_fields(),
        "m1_importance": "HIGH",
        "m1_summary": "An external app received account access.",
        "m1_reason": "Review the changed account permissions.",
        "m1_source_ref": "m1p1",
    }
    missing = {key: value for key, value in valid.items() if key != "m1_reason"}
    unsupported = {**valid, "m1_source_ref": ""}
    wrong_language = {**valid, "m1_summary": "검토할 새 계정 권한이 있어요."}

    class LayeredFactory(FieldFactory):
        def create(self, role, plan):
            model = super().create(role, plan)
            if role == "mail_interest_verifier":
                model.plan = ModelPlan(
                    steps=tuple(
                        ToolStep("MailFieldAssessment", payload)
                        for payload in [missing, unsupported, wrong_language, valid]
                    ),
                    output=plan.output,
                )
            return model

    factory = LayeredFactory(valid)
    result = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]), [record()], factory
    )
    assert result[0].summary == valid["m1_summary"]
    assert factory.models["mail_interest_verifier"].stream_calls == 4
    assert json.loads(capsys.readouterr().out) == {
        "event": "mail_interest_output_repaired",
        "role": "mail_interest_verifier",
        "attempts": 4,
        "failures": 3,
    }


def test_incomplete_draft_is_reviewed_but_cannot_replace_a_complete_final_result():
    draft = {**negative_fields(), "m1_importance": "HIGH"}
    reviewed = {
        **draft,
        "m1_summary": "An external app received account access.",
        "m1_reason": "Review the account permission change.",
        "m1_source_ref": "m1p1",
    }
    factory = Factory(draft, reviewed)
    result = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]), [record()], factory
    )
    assert result[0].summary == reviewed["m1_summary"]
    assert all(model.stream_calls == 1 for model in factory.models.values())


def test_final_review_reports_all_missing_support_and_copy_fields_together(capsys):
    draft = {**negative_fields("m1"), **negative_fields("m2")}
    bad = {
        **draft,
        "m1_importance": "HIGH",
        "m1_reason": "Review account permissions.",
        "m2_importance": "HIGH",
        "m2_summary": "계정 권한을 확인해 주세요.",
        "m2_source_ref": "m2p1",
    }
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]),
            [record(), record("gmail:second")],
            Factory(draft, bad),
        )
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["role"] == "mail_interest_verifier"
    assert diagnostic["failures"] == 4
    expected = {"m1_source_ref", "m1_summary", "m2_summary", "m2_reason"}
    assert {
        error["loc"][0] for error in diagnostic["tool_validation_errors"]
    } == expected
    assert len(diagnostic["tool_validation_errors"]) == len(expected) * 4


def test_explicit_source_requirements_reach_actual_output_schema_and_final_result():
    budget = record(
        "gmail:budget",
        text="You requested that we alert you. Budget Type: Cost\nBudgeted Amount: $1.00\nAlert Type: FORECASTED\nFORECASTED Amount: $1.15",
    ).model_copy(
        update={
            "title": "AWS Budgets: Account example exceeds alert threshold",
            "facts": ["sender_domain=costalerts.amazonaws.com"],
        }
    )
    deadline = record(
        "gmail:deadline",
        text="There are only 3 days left to complete your submission for Example Hackathon. You received this email because you signed up for Example Hackathon.",
    ).model_copy(
        update={
            "title": "Final call for submissions - Example Hackathon",
            "facts": ["sender_domain=devpost.com"],
        }
    )
    deletion = record(
        "gmail:deletion",
        text="회원님의 계정이 삭제될 예정입니다. 삭제예정일 2026-10-07\n개인정보와 잔액은 모두 삭제 처리되어 복구할 수 없습니다.",
    ).model_copy(update={"title": "계정 삭제 안내"})
    payload = {
        **negative_fields("m1"),
        **negative_fields("m2"),
        **negative_fields("m3"),
        "m1_importance": "HIGH",
        "m1_summary": "Forecast cost is $1.15, above the $1.00 budget.",
        "m1_reason": "Forecast cost exceeds the configured budget.",
        "m1_source_ref": "m1p1",
        "m2_importance": "HIGH",
        "m2_summary": "A submission reminder for the registered Example Hackathon. When the email was sent, 3 days remained.",
        "m2_reason": "Review the registered event’s submission deadline.",
        "m2_source_ref": "m2p1",
        "m3_importance": "HIGH",
        "m3_summary": "The account will be deleted on 2026-10-07. Personal data and the balance will be lost and cannot be recovered.",
        "m3_reason": "Review the deadline to avoid losing the account and balance.",
        "m3_source_ref": "m3p1",
    }
    factory = FieldFactory(payload)
    matches = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림", "데이터셋"]),
        [budget, deadline, deletion],
        factory,
    )
    assert [m.evidence_ref for m in matches] == [budget.ref, deadline.ref, deletion.ref]
    schema = factory.models["mail_interest_verifier"].schema["properties"]
    assert all(schema[f"m{i}_importance"]["const"] == "HIGH" for i in (1, 2, 3))
    assert schema["m1_tag_refs"]["const"] == schema["m2_tag_refs"]["const"] == ""


def test_google_profile_sharing_cannot_use_dataset_tag_even_for_an_ai_service():
    source = record(
        text="이름 및 프로필 사진과 이메일 주소가 공유되었습니다. 이 프로필 정보를 받았습니다."
    ).model_copy(
        update={
            "title": "Google 계정 데이터 일부를 dataset-service.test에 공유하셨습니다",
            "facts": ["sender_domain=google.com"],
        }
    )
    wrong = {
        **negative_fields(),
        "m1_tag_refs": "t2",
        "m1_importance": "NORMAL",
        "m1_summary": "Profile information was shared.",
        "m1_reason": "A notice about profile sharing.",
        "m1_source_ref": "m1p1",
    }
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림", "데이터셋"]),
            [source],
            FieldFactory(wrong),
        )
