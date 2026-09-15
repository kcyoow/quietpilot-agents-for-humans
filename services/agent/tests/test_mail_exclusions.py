from __future__ import annotations

import json

import pytest
from quietpilot_agent.local_model import DeterministicModel, ModelPlan, ToolStep
from quietpilot_agent.mail_interests import (
    MailDecision,
    MailInterestOutputError,
    MailInterestProfile,
    match_interest_mail,
)
from quietpilot_agent.models import EvidenceRecord


def _source():
    return EvidenceRecord(
        user_id="synthetic-owner",
        ref="synthetic-owned-mail",
        revision=1,
        source="gmail",
        title="오프라인 연구 세미나 안내",
        facts=["sender_domain=synthetic.example"],
        untrusted_text="이번 연구 세미나는 오프라인으로 진행합니다. 온라인 중계는 없습니다.",
    )


def _profile(description="오프라인 세미나는 제외하고 온라인 행사만 보여 줘."):
    return MailInterestProfile(revision=1, tags=["세미나"], description=description)


def _fields(*, excluded="NO", importance="NORMAL"):
    return {
        "m1_tag_refs": "t1",
        "m1_description_match": "NO",
        "m1_excluded": excluded,
        "m1_importance": importance,
        "m1_summary": "An in-person research seminar notice.",
        "m1_reason": "Describes the format of a seminar matching your interest.",
        "m1_source_ref": "m1p1",
    }


class _SchemaModel(DeterministicModel):
    async def stream(self, messages, tool_specs=None, *args, **kwargs):
        self.schema = tool_specs[0]["inputSchema"]["json"]
        async for event in super().stream(messages, tool_specs, *args, **kwargs):
            yield event


class _Factory:
    def __init__(self, draft, reviewed=None):
        self.payloads = {
            "mail_interest_matcher": draft,
            "mail_interest_verifier": draft if reviewed is None else reviewed,
        }
        self.models = {}

    def create(self, role, plan):
        model = _SchemaModel(
            role,
            ModelPlan(
                steps=(ToolStep("MailFieldAssessment", self.payloads[role]),) * 4,
                output=plan.output,
            ),
        )
        self.models[role] = model
        return model


@pytest.mark.parametrize("importance", ["NORMAL", "HIGH"])
@pytest.mark.parametrize(
    "description",
    [
        "오프라인 세미나는 제외해 줘.",
        "온라인 행사만 보여 줘.",
        "참여하려면 온라인 중계가 반드시 있어야 해.",
    ],
)
def test_explicit_exclusion_overrides_topic_and_importance_without_relabeling(
    importance, description
):
    payload = _fields(excluded="YES", importance=importance)
    factory = _Factory(payload)
    assert match_interest_mail(_profile(description), [_source()], factory) == []
    assert list(factory.models) == ["mail_interest_matcher", "mail_interest_verifier"]
    for model in factory.models.values():
        assert model.stream_calls == 1
        assert model.plan.steps[0].input["m1_importance"] == importance
        assert model.plan.steps[0].input["m1_tag_refs"] == "t1"
        assert model.plan.steps[0].input["m1_excluded"] == "YES"


def test_additional_positive_interest_remains_an_or_alternative_and_api_is_unchanged():
    factory = _Factory(_fields())
    matches = match_interest_mail(
        _profile("데이터셋 출시 소식도 함께 보고 싶어요."), [_source()], factory
    )
    assert len(matches) == 1
    assert matches[0].matched_tags == ["세미나"]
    assert matches[0].importance == "NORMAL"
    assert matches[0].evidence_ref == _source().ref
    assert set(matches[0].model_dump()) == {
        "evidence_ref",
        "summary",
        "reason",
        "matched_tags",
        "importance",
    }


def test_description_only_positive_interest_still_selects_without_a_tag():
    payload = {**_fields(), "m1_tag_refs": "", "m1_description_match": "YES"}
    matches = match_interest_mail(
        MailInterestProfile(revision=1, description="연구 세미나 소식을 보여 줘."),
        [_source()],
        _Factory(payload),
    )
    assert len(matches) == 1 and matches[0].matched_tags == []


@pytest.mark.parametrize(
    "draft_excluded,reviewed_excluded", [("YES", "NO"), ("NO", "YES")]
)
def test_independent_verifier_can_correct_either_direction_of_draft_exclusion(
    draft_excluded, reviewed_excluded
):
    factory = _Factory(
        _fields(excluded=draft_excluded, importance="HIGH"),
        _fields(excluded=reviewed_excluded, importance="HIGH"),
    )
    matches = match_interest_mail(_profile(), [_source()], factory)
    assert len(matches) == (0 if reviewed_excluded == "YES" else 1)
    if matches:
        assert matches[0].importance == "HIGH"
    draft, reviewed = factory.models.values()
    draft_prompt = json.loads(draft.received_messages[0][0]["content"][0]["text"])
    reviewed_prompt = json.loads(reviewed.received_messages[0][0]["content"][0]["text"])
    assert reviewed_prompt["evidence"] == draft_prompt["evidence"]
    assert reviewed_prompt["draft"]["m1_excluded"] == draft_excluded
    assert all(model.stream_calls == 1 for model in factory.models.values())


@pytest.mark.parametrize("role", ["mail_interest_matcher", "mail_interest_verifier"])
@pytest.mark.parametrize(
    "value",
    [None, True, "yes", "UNKNOWN", [], {"PRIVATE-SENTINEL": "PRIVATE-SENTINEL"}],
)
def test_invalid_exclusion_field_is_rejected_by_actual_sdk_in_either_pass(
    role, value, capsys
):
    valid = _fields()
    invalid = {**valid, "m1_excluded": value}
    factory = _Factory(
        invalid if role == "mail_interest_matcher" else valid,
        invalid if role == "mail_interest_verifier" else valid,
    )
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(_profile(), [_source()], factory)
    assert factory.models[role].stream_calls == 4
    if role == "mail_interest_matcher":
        assert list(factory.models) == [role]
    logs = capsys.readouterr().out
    assert "PRIVATE-SENTINEL" not in logs
    assert "synthetic-owned-mail" not in logs
    assert "온라인 중계는 없습니다" not in logs
    diagnostic = json.loads(logs.splitlines()[-1])
    assert any(
        error["loc"] == ["m1_excluded"]
        for error in diagnostic["tool_validation_errors"]
    )


@pytest.mark.parametrize("role", ["mail_interest_matcher", "mail_interest_verifier"])
def test_missing_exclusion_field_has_no_default_in_model_facing_schema(role):
    valid = _fields()
    invalid = dict(valid)
    del invalid["m1_excluded"]
    factory = _Factory(
        invalid if role == "mail_interest_matcher" else valid,
        invalid if role == "mail_interest_verifier" else valid,
    )
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(_profile(), [_source()], factory)
    assert factory.models[role].stream_calls == 4


@pytest.mark.parametrize("role", ["mail_interest_matcher", "mail_interest_verifier"])
def test_empty_description_disallows_excluded_yes_and_advertises_const_no(role):
    valid = _fields()
    invalid = _fields(excluded="YES")
    factory = _Factory(
        invalid if role == "mail_interest_matcher" else valid,
        invalid if role == "mail_interest_verifier" else valid,
    )
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(_profile(""), [_source()], factory)
    schema = factory.models[role].schema
    assert schema["properties"]["m1_excluded"]["const"] == "NO"
    assert schema["properties"]["m1_excluded"]["type"] == "string"
    assert set(schema["required"]) == set(_fields())
    assert len(schema["required"]) == 7


@pytest.mark.parametrize(
    "field,value", [("m1_source_ref", "m2p1"), ("m1_tag_refs", "t2")]
)
def test_exclusion_does_not_bypass_source_ownership_or_saved_tag_constraints(
    field, value
):
    factory = _Factory({**_fields(excluded="YES"), field: value})
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(_profile(), [_source()], factory)
    assert list(factory.models) == ["mail_interest_matcher"]


def test_nonmatching_normal_mail_can_be_unselected_without_an_explicit_exclusion():
    payload = {
        **_fields(),
        "m1_tag_refs": "",
        "m1_summary": "",
        "m1_reason": "",
        "m1_source_ref": "",
    }
    assert match_interest_mail(_profile(), [_source()], _Factory(payload)) == []


def test_internal_decision_default_stays_backward_compatible():
    decision = MailDecision(
        evidence_ref="m1",
        matched_tags=[],
        description_match=False,
        importance="LOW",
        summary="",
        reason="",
        supporting_quotes=[],
    )
    assert decision.excluded is False
