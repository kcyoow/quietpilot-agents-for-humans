from __future__ import annotations

import runpy
from pathlib import Path

import pytest
from quietpilot_agent.mail_interests import (
    MailInterestOutputError,
    MailInterestProfile,
    match_interest_mail,
)

helpers = runpy.run_path(str(Path(__file__).with_name("test_mail_quality.py")))
Factory = helpers["Factory"]
record = helpers["record"]
negative_fields = helpers["negative_fields"]


def selected_fields():
    return {
        **negative_fields(),
        "m1_tag_refs": "t1",
        "m1_importance": "HIGH",
        "m1_summary": "Review the account access change.",
        "m1_reason": "A notice about changed account permissions.",
        "m1_source_ref": "m1p1",
    }


@pytest.mark.parametrize(("field", "bound"), [("summary", 220), ("reason", 180)])
def test_actual_sdk_rejects_overlong_display_copy_without_truncating(field, bound):
    valid = selected_fields()
    invalid = {**valid, f"m1_{field}": "길" * (bound + 1)}
    factory = Factory(valid, invalid)
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["보안알림"]), [record()], factory
        )
    reviewer = factory.models["mail_interest_verifier"]
    assert reviewer.schema["properties"][f"m1_{field}"]["maxLength"] == bound
    assert reviewer.stream_calls == 4


def test_final_display_redacts_model_supplied_credentials_in_summary_and_reason():
    fields = {
        **selected_fields(),
        "m1_summary": "The account notice contains verification code: 2468. The date is 2026-10-07 and the amount is $1.15.",
        "m1_reason": "An account change notice with verification code: 1357.",
    }
    factory = Factory(fields, fields)
    matches = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]), [record()], factory
    )
    assert len(matches) == 1
    rendered = matches[0]
    assert "2468" not in rendered.summary and "1357" not in rendered.reason
    assert "[REDACTED]" in rendered.summary
    assert "[REDACTED]" in rendered.reason
    assert "2026-10-07" in rendered.summary and "$1.15" in rendered.summary
    assert rendered.evidence_ref == "gmail:owned"
    assert rendered.importance == "HIGH"


def test_verbose_internal_draft_can_be_shortened_by_the_independent_reviewer():
    final = selected_fields()
    draft = {**final, "m1_summary": "Account access changed. " * 15}
    assert 220 < len(draft["m1_summary"]) <= 500
    factory = Factory(draft, final)
    matches = match_interest_mail(
        MailInterestProfile(revision=1, tags=["보안알림"]), [record()], factory
    )
    assert matches[0].summary == final["m1_summary"]
    assert factory.models["mail_interest_matcher"].stream_calls == 1
    assert factory.models["mail_interest_verifier"].stream_calls == 1
