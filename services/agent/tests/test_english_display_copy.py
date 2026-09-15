from __future__ import annotations

import pytest
from quietpilot_agent.discovery_copy import (
    CopyLanguageError,
    CopyTemporalError,
    validate_display_copy,
    validate_temporal_copy,
)
from quietpilot_agent.mail_content import redact_credentials, redact_display_credentials


@pytest.mark.parametrize(
    "text,source",
    [
        (
            "Prepare the application documents by 2030-05-01.",
            "2030-05-01까지 신청서 제출",
        ),
        (
            "The source says ‘추가 조치가 필요하지 않습니다’.",
            "추가 조치가 필요하지 않습니다",
        ),
        ("When the email was sent, it said '3일 남음'.", "3일 남음"),
        ("Review the 東京ディズニーランド booking.", "東京ディズニーランド 예약 확정"),
    ],
)
def test_new_english_copy_preserves_korean_and_named_original_sources(text, source):
    before = (text, source)
    validate_display_copy(text, evidence_texts=(source,), language="en")
    validate_temporal_copy(text, evidence_texts=(source,))
    assert (text, source) == before


@pytest.mark.parametrize(
    "text",
    ["새로 생성된 한국어 설명이에요.", "Review 删除.", "Review проверка.", "12345"],
)
def test_new_english_mode_does_not_accept_other_generated_languages_or_empty_prose(
    text,
):
    with pytest.raises(CopyLanguageError, match="^copy_korean_required$"):
        validate_display_copy(text, evidence_texts=(), language="en")


def test_legacy_korean_copy_and_literal_source_titles_remain_readable():
    legacy = "신청 서류를 준비해요."
    validate_display_copy(legacy, evidence_texts=(), language="ko")
    source_title = "서울대학교"
    validate_display_copy(
        source_title,
        evidence_texts=(source_title,),
        language="en",
        allow_source_name=True,
    )
    assert legacy == "신청 서류를 준비해요." and source_title == "서울대학교"


@pytest.mark.parametrize(
    "source,label",
    [
        ("Password: SYNTHETIC-SECRET-VALUE", "Password"),
        ("ID/PW: synthetic-user SYNTHETIC-SECRET-VALUE", "ID/PW"),
    ],
)
def test_english_redaction_preserves_the_legacy_source_normalization_contract(
    source, label
):
    normalized_source = redact_credentials(source)
    assert normalized_source == f"{label}: [자격정보삭제]"
    display = redact_display_credentials(source)
    assert display == f"{label}: [REDACTED]"
    assert redact_display_credentials(display) == display
    assert redact_credentials(source) == normalized_source
    validate_display_copy(display, evidence_texts=(), language="en")


@pytest.mark.parametrize(
    "text,source",
    [
        ("The deadline is in 3 days.", "3 days left"),
        ("The email says '3일 남음'.", "3일 남음"),
        ("When the email was sent, it said '4일 남음'.", "3일 남음"),
        (
            "When the email was sent, it said '3일 남음'. It is due tomorrow.",
            "3일 남음",
        ),
    ],
)
def test_english_copy_still_rejects_current_or_unsupported_relative_time(text, source):
    with pytest.raises(CopyTemporalError, match="^copy_temporal_relative$"):
        validate_temporal_copy(text, evidence_texts=(source,))
