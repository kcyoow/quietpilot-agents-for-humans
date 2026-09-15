from __future__ import annotations

import base64
from email.header import Header
from urllib.parse import parse_qs, urlparse

import pytest
from quietpilot_agent.google_connector import GoogleConnector, _safe_header
from quietpilot_agent.mail_content import (
    MAX_MIME_DEPTH,
    MAX_SOURCE_TEXT_CHARS,
    REDACTED,
    extract_mail_source,
    redact_credentials,
)


def part(text: str, mime: str = "text/plain", charset: str = "utf-8", **extra):
    return {
        "mimeType": mime,
        "headers": [{"name": "Content-Type", "value": f"{mime}; charset={charset}"}],
        "body": {
            "data": base64.urlsafe_b64encode(text.encode(charset)).decode().rstrip("=")
        },
        **extra,
    }


def test_plain_body_preserves_forecast_deadline_and_no_action_clauses():
    body = "장학금 모집은 10월로 예상합니다. 아직 확정된 마감은 없습니다.\n현재 신청하거나 답장할 필요는 없습니다.\n확정 안내는 9월 30일에 발송합니다."
    result = extract_mail_source(part(body), "장학금 모집 일정 안내")
    assert result.source_content == "body"
    assert not result.truncated
    assert body in result.text
    assert "장학금 모집 일정 안내" in result.text


@pytest.mark.parametrize(
    "missing",
    [
        {"attachmentId": "unfetched-inline-body", "size": 5000},
        {"data": "not!base64", "size": 5000},
        {"size": 5000},
    ],
)
def test_unread_inline_text_cannot_be_reported_as_complete(missing):
    result = extract_mail_source(
        {
            "mimeType": "multipart/mixed",
            "parts": [
                part("This is only the first part of the message."),
                {"mimeType": "text/plain", "body": missing},
            ],
        },
        None,
    )
    assert result.source_content == "body"
    assert result.truncated


@pytest.mark.parametrize("plain_first", [True, False])
@pytest.mark.parametrize("bad_html", ["oversized", "encoding"])
def test_complete_alternative_does_not_inherit_discarded_branch_loss(
    plain_first, bad_html
):
    html = part("x" * 100_000, "text/html")
    if bad_html == "encoding":
        html["body"]["data"] = "not!base64"
    children = [part("No action is required."), html]
    result = extract_mail_source(
        {
            "mimeType": "multipart/alternative",
            "parts": children[:: 1 if plain_first else -1],
        },
        None,
    )
    assert result.text == "No action is required." and not result.truncated


def test_mixed_bodies_preserve_unique_html_facts_and_alternative_can_fall_back():
    result = extract_mail_source(
        {
            "mimeType": "multipart/mixed",
            "parts": [
                part("Account policy."),
                part("<p>No action is required.</p>", "text/html"),
            ],
        },
        None,
    )
    assert "Account policy." in result.text
    assert "No action is required." in result.text
    assert not result.truncated
    result = extract_mail_source(
        {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"attachmentId": "unread"}},
                part("<p>Complete HTML body.</p>", "text/html"),
            ],
        },
        None,
    )
    assert result.text == "Complete HTML body." and not result.truncated


@pytest.mark.parametrize("loss", ["invalid_utf8", "unknown_charset", "size_mismatch"])
def test_lossy_decode_and_mismatched_body_size_are_incomplete(loss):
    payload = part("Account policy.")
    if loss == "invalid_utf8":
        payload["body"]["data"] = base64.urlsafe_b64encode(b"Details \xff").decode()
    elif loss == "unknown_charset":
        payload["headers"][0]["value"] = "text/plain; charset=not-a-charset"
    else:
        payload["body"]["size"] = 999
    assert extract_mail_source(payload, None).truncated


@pytest.mark.parametrize("extra", [0, 1])
def test_selected_source_limit_preserves_exact_boundary_and_marks_overflow(extra):
    from quietpilot_agent.models import MAX_CASE_SOURCE_TEXT_CHARS

    result = extract_mail_source(
        part("x" * (MAX_CASE_SOURCE_TEXT_CHARS + extra)),
        None,
        max_chars=MAX_CASE_SOURCE_TEXT_CHARS,
    )
    assert len(result.text) == MAX_CASE_SOURCE_TEXT_CHARS
    assert result.truncated is bool(extra)


def test_nested_plain_is_preferred_and_attachment_or_images_are_not_read():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    part("<p>중복 HTML</p>", "text/html"),
                    part("신청 마감은 9월 30일입니다. 답장은 필요 없습니다."),
                ],
            },
            part("ATTACHMENT-SECRET", filename="notice.txt"),
            part("INLINE-IMAGE-SECRET", "image/png"),
            part(
                "DISPOSITION-SECRET",
                headers=[
                    {
                        "name": "Content-Disposition",
                        "value": "attachment; filename=x.txt",
                    }
                ],
            ),
        ],
    }
    result = extract_mail_source(payload, None)
    assert result.source_content == "body"
    assert result.text == "신청 마감은 9월 30일입니다. 답장은 필요 없습니다."


def test_html_text_skips_nonvisible_content_and_keeps_table_credentials_redactable():
    payload = part(
        """<head><title>HEAD-SECRET</title></head><style>STYLE-SECRET</style>
    <script>SCRIPT-SECRET</script><p>9월 30일까지 신청해 주세요.</p>
    <img alt="IMAGE-SECRET" src="https://example.invalid/secret">
    <table><tr><td>ID</td><td>student@school.example</td></tr><tr><td>PW</td><td>SchoolTemp123!</td></tr></table>
    <p>기존 신청자는 추가 조치가 필요 없습니다.</p>""",
        "text/html",
    )
    result = extract_mail_source(payload, None)
    assert "9월 30일까지" in result.text
    assert "기존 신청자는 추가 조치가 필요 없습니다." in result.text
    for secret in [
        "HEAD-SECRET",
        "STYLE-SECRET",
        "SCRIPT-SECRET",
        "IMAGE-SECRET",
        "student@school.example",
        "SchoolTemp123!",
    ]:
        assert secret not in result.text


def test_declared_korean_charset_is_decoded():
    assert (
        extract_mail_source(part("신청은 필요 없습니다.", charset="euc-kr"), None).text
        == "신청은 필요 없습니다."
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"mimeType": "text/plain", "body": {"data": "!!invalid!!"}},
        {
            "mimeType": "text/plain",
            "body": {"attachmentId": "not-fetched", "data": "U0VDUkVU"},
        },
    ],
)
def test_missing_or_unreadable_inline_body_uses_redacted_snippet(payload):
    result = extract_mail_source(payload, "인증번호123456입니다. 답장은 필요 없습니다.")
    assert result.source_content == "snippet_only"
    assert "123456" not in result.text
    assert "답장은 필요 없습니다." in result.text


def test_multipart_depth_and_part_count_are_bounded():
    deep = part("TOO-DEEP")
    for _ in range(MAX_MIME_DEPTH + 2):
        deep = {"mimeType": "multipart/mixed", "parts": [deep]}
    result = extract_mail_source(deep, "미리보기")
    assert result.text == "미리보기"
    assert result.truncated
    wide = {"mimeType": "multipart/mixed", "parts": [part(str(i)) for i in range(100)]}
    assert extract_mail_source(wide, None).truncated


def test_redaction_precedes_final_source_truncation():
    body = "안내 " * 1250 + "비밀번호: SchoolTemp123!\n" + "내용 " * 2000
    result = extract_mail_source(part(body), "")
    assert len(result.text) == MAX_SOURCE_TEXT_CHARS
    assert result.truncated
    assert "SchoolTemp123" not in result.text


@pytest.mark.parametrize(
    "text,secrets",
    [
        ("인증번호123456입니다. 유효시간은 10분입니다.", ["123456"]),
        ("인증번호는 123 456입니다.", ["123 456"]),
        ("OTP: 123-456", ["123-456"]),
        ("123456은 인증코드입니다.", ["123456"]),
        ("Your verification code: 654321", ["654321"]),
        (
            "비밀번호: SchoolTemp123!\nID: student@school.example",
            ["SchoolTemp123!", "student@school.example"],
        ),
        (
            "ID / PW: student@school.example / SchoolTemp123!",
            ["SchoolTemp123!", "student@school.example"],
        ),
        (
            "ID\nstudent@school.example\nPW\nSchoolTemp123!",
            ["SchoolTemp123!", "student@school.example"],
        ),
        (
            "ID + PW\nstudent@school.example\nSchoolTemp123!",
            ["SchoolTemp123!", "student@school.example"],
        ),
        (
            "ID/PW\nstudent@school.example / SchoolTemp123!",
            ["SchoolTemp123!", "student@school.example"],
        ),
        (
            "학교 계정 student@school.example\n비밀번호는SchoolTemp123!",
            ["SchoolTemp123!", "student@school.example"],
        ),
        ("로그인 비밀번호 !Temporary123", ["!Temporary123"]),
        (
            "API key: local-private-key-123\nAKIAABCDEFGHIJKLMNOP\nsk-proj-abcdefghijklmnop1234567890",
            [
                "local-private-key-123",
                "AKIAABCDEFGHIJKLMNOP",
                "sk-proj-abcdefghijklmnop1234567890",
            ],
        ),
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature123",
            ["eyJhbGciOiJIUzI1NiJ9", "signature123"],
        ),
        (
            "확인: https://school.example/reset?code=private-return-code&email=student%40school.example",
            ["private-return-code", "student%40school.example"],
        ),
        (
            "파일 https://files.example/a?X-Amz-Credential=private-value&X-Amz-Signature=private-signature",
            ["private-value", "private-signature"],
        ),
        (
            "로그인 https://school.example/login#access_token=private-fragment-token",
            ["private-fragment-token"],
        ),
    ],
)
def test_detected_credentials_are_removed_without_removing_next_line_deadline(
    text, secrets
):
    result = redact_credentials(
        text + "\n신청 마감은 9월 30일이며 별도 회신은 필요 없습니다."
    )
    for secret in secrets:
        assert secret not in result
    assert "신청 마감은 9월 30일이며 별도 회신은 필요 없습니다." in result


def test_ordinary_dates_and_public_url_query_are_preserved():
    text = "2026년 9월 30일 마감 예상이며 아직 확정되지 않았습니다. https://school.example/news?category=scholarship"
    assert redact_credentials(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "password changed",
        "Reset password request",
        "password has changed",
        "Your password changed.",
        "Password reset request!",
        "비밀번호는 변경되었습니다.",
        "비밀번호는 변경되지 않았습니다.",
        "비밀번호는 변경될 예정입니다.",
        "비밀번호는2026년 9월 30일에 변경될 예정입니다.",
        "암호는 변경되지 않았으며 별도 조치가 필요 없습니다.",
    ],
)
def test_password_event_prose_keeps_its_qualifier(text):
    assert redact_credentials(text) == text


@pytest.mark.parametrize(
    "text,secret",
    [
        ("비밀번호: 새비밀번호값", "새비밀번호값"),
        ("비밀번호는임시1234!", "임시1234!"),
    ],
)
def test_explicit_korean_password_values_still_redact(text, secret):
    assert secret not in redact_credentials(text)


def test_subject_credentials_are_removed_before_title_limit():
    assert "123456" not in _safe_header("인증번호123456입니다", "")


class GoogleFixture:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request_json(self, method, url, **kwargs):
        assert method == "GET"
        assert parse_qs(urlparse(url).query) == {
            "format": ["full"],
            "fields": ["id,internalDate,snippet,payload"],
        }
        self.calls.append(url)
        return self.response


def test_connector_reads_body_once_and_keeps_only_domain_identity_and_limit_facts():
    payload = part(
        "9월 30일은 예상 마감입니다. 지금 조치할 필요는 없습니다.\nPW: SchoolTemp123!"
    )
    payload["headers"] += [
        {"name": "Subject", "value": "장학금 안내"},
        {"name": "From", "value": "Office <person@school.example>"},
    ]
    google = GoogleFixture(
        {
            "id": "message-1",
            "internalDate": "1788000000000",
            "snippet": "장학금 모집 안내",
            "payload": payload,
        }
    )
    connector = GoogleConnector(
        identity=object(),
        google=google,
        oauth_return_url="https://example.invalid/return",
    )
    evidence = connector._message_evidence(
        access_token="synthetic-token", message_id="message-1"
    )
    assert len(google.calls) == 1
    assert evidence["facts"] == [
        "received_at_unix_ms=1788000000000",
        "sender_domain=school.example",
        "source_content=body",
    ]
    assert "지금 조치할 필요는 없습니다." in evidence["untrusted_text"]
    assert "SchoolTemp123!" not in str(evidence)
    assert "person@school.example" not in str(evidence)


def test_connector_still_rejects_a_different_returned_message_id():
    google = GoogleFixture(
        {"id": "different", "internalDate": "1788000000000", "payload": part("안내")}
    )
    connector = GoogleConnector(
        identity=object(),
        google=google,
        oauth_return_url="https://example.invalid/return",
    )
    with pytest.raises(RuntimeError, match="does not match"):
        connector._message_evidence(
            access_token="synthetic-token", message_id="message-1"
        )


@pytest.mark.parametrize(
    "text",
    [
        "로그인 코드: 4827입니다.",
        "일회성 코드는 4827입니다.",
        "일회성 번호 4827",
        "로그인에 필요한 코드는 4827입니다.",
        "인증을 위한 코드: 4827",
        "인증 코드를 입력하세요:\n4827",
        "로그인 인증 번호는\n4\n8\n2\n7\n입니다.",
        "인증 코드\n48\n27",
        "Your sign-in code is 4827.",
        "Your authentication code: 4827.",
        "Your code to sign in: 4827.",
        "4827은 로그인 코드입니다.",
        "4827 is your one-time code.",
        "일회성 코드: 4\u200b8\u200b2\u200b7",
    ],
)
def test_four_digit_otp_contexts_redact_value_and_keep_event_facts(text):
    facts = "\n유효시간은 15분이며 발급일은 2026년 9월 14일입니다. 금액은 5000원입니다.\n비밀번호가 변경되었습니다. 계정 변경은 요청하지 않았습니다."
    result = redact_credentials(text + facts)
    assert "4827" not in result
    assert REDACTED in result
    assert facts in result
    assert redact_credentials(result) == result


@pytest.mark.parametrize(
    "body,mime",
    [
        ("로그인 코드\n4827\n유효시간은 15분입니다.", "text/plain"),
        (
            "<table><tr><td>로그인 코드</td></tr><tr><td>4</td><td>8</td><td>2</td><td>7</td></tr></table><p>유효시간은 15분입니다.</p>",
            "text/html",
        ),
        (
            "<table><tr><th>일회성 코드</th><td>48</td><td>27</td></tr></table><p>유효시간은 15분입니다.</p>",
            "text/html",
        ),
    ],
)
def test_mime_extraction_redacts_split_otp_and_preserves_the_duration(body, mime):
    source = extract_mail_source(part(body, mime), None)
    assert REDACTED in source.text
    assert "4827" not in "".join(source.text.split())
    assert "15분" in source.text


@pytest.mark.parametrize(
    "subject", ["Your login code", Header("로그인 코드 안내", "utf-8").encode()]
)
def test_subject_otp_context_redacts_bare_body_code_without_erasing_dates(subject):
    payload = part(
        "안녕하세요.\n4827\n2026년 9월 14일 발급.\n15분간 유효합니다.\n5000원 결제 안내."
    )
    payload["headers"].append({"name": "Subject", "value": subject})
    source = extract_mail_source(payload, None)
    assert "4827" not in source.text and REDACTED in source.text
    assert "2026년 9월 14일" in source.text
    assert "15분간" in source.text
    assert "5000원" in source.text


@pytest.mark.parametrize(
    "attribute",
    [
        "hidden",
        'aria-hidden="true"',
        'style="display: none !important"',
        'style="visibility: hidden"',
        'style="mso-hide: all"',
        'style="opacity:0"',
    ],
)
def test_hidden_html_descendants_and_siblings_never_become_model_evidence(attribute):
    body = f"<div {attribute}><div>HIDDEN-INNER-PRIVATE</div>HIDDEN-SIBLING-PRIVATE<br>HIDDEN-AFTER-BR</div><p>비밀번호가 변경되었습니다. 2026년 9월 14일, 5000원입니다.</p>"
    source = extract_mail_source(part(body, "text/html"), None)
    assert "HIDDEN" not in source.text
    assert source.text == "비밀번호가 변경되었습니다. 2026년 9월 14일, 5000원입니다."


@pytest.mark.parametrize(
    "link",
    [
        "https://accounts.example.test/password-reset/A7mQ9nT2vR5pL8wK",
        "https://accounts.example.test/password-reset/A7mQ9nT2vR5pL8wK).",
        "https://accounts.example.test/password/reset/A7mQ9nT2vR5pL8wK",
        "https://accounts.example.test/magic-link/A7mQ9nT2vR5pL8wK",
        "https://accounts.example.test/login#A7mQ9nT2vR5pL8wK",
        "https://accounts.example.test/#/reset-password/A7mQ9nT2vR5pL8wK",
        "https://accounts.example.test/verify-email?t=A7mQ9nT2vR5pL8wK",
        "https://accounts.example.test/reset%2Fpassword%2FA7mQ9nT2vR5pL8wK",
        "Magic link: https://accounts.example.test/r/A7mQ9nT2vR5pL8wK",
        "비밀번호 재설정 링크: https://accounts.example.test/r/A7mQ9nT2vR5pL8wK",
    ],
)
def test_mime_authentication_links_remove_path_query_and_fragment_secrets(link):
    facts = "비밀번호가 변경되었습니다. 2026년 9월 14일, 5000원입니다."
    source = extract_mail_source(part(link + "\n" + facts), None)
    assert "A7mQ9nT2vR5pL8wK" not in source.text
    assert facts in source.text


def test_otp_and_date_in_the_same_context_redact_only_the_code():
    text = "일회성 코드 4827 - 2026-09-14 발급, 15분 후 만료, 금액 5000원."
    result = redact_credentials(text)
    assert "4827" not in result
    assert "2026-09-14" in result and "15분" in result and "5000원" in result
    for text in [
        "인증 코드는 2026년 9월 14일부터 제공됩니다.",
        "확인 코드는 5000원 결제 건에 적용됩니다.",
        "로그인 코드는 15분간 유효합니다.",
        "비밀번호가 변경되었습니다.",
        "계정이 변경되지 않았습니다.",
        "공개 문서 https://accounts.example.test/help/password-reset",
    ]:
        assert redact_credentials(text) == text


def test_display_text_can_be_redacted_again_with_subject_context():
    summary = "요청한 일회성 코드는 4827이며, 15분간 유효해요."
    reason = "비밀번호가 변경되었고 금액 5000원과 날짜 2026-09-14를 확인해야 해요."
    assert "4827" not in redact_credentials(summary)
    assert redact_credentials(reason) == reason
    assert redact_credentials("4827", context="로그인 코드 안내") == REDACTED
    assert redact_credentials("4827", context="로그인&#32;코드 안내") == REDACTED
    assert redact_credentials("일반 번호 4827") == "일반 번호 4827"


@pytest.mark.parametrize(
    "label",
    [
        "password reset token:",
        "reset secret:",
        "magic link token",
        "비밀번호 재설정 토큰:",
    ],
)
def test_reset_secret_fields_are_redacted_without_removing_event_prose(label):
    facts = "비밀번호가 변경되었습니다. 금액 5000원, 날짜 2026년 9월 14일입니다."
    result = extract_mail_source(part(f"{label} A7mQ9nT2vR5pL8wK\n{facts}"), None)
    assert "A7mQ9nT2vR5pL8wK" not in result.text
    assert facts in result.text


@pytest.mark.parametrize("separator", [": ", "\n"])
def test_paired_credentials_do_not_remove_same_line_amount_and_date(separator):
    facts = "; 비밀번호가 변경되었습니다. 금액 5000원, 2026-09-14입니다."
    result = redact_credentials(
        "ID/PW" + separator + "learner@school.example / SchoolTemp4827!" + facts
    )
    assert "learner@school.example" not in result
    assert "SchoolTemp4827!" not in result
    assert facts in result


@pytest.mark.parametrize(
    "prefix,values",
    [
        ("ID/PW: ", "sampleuser Pass123!"),
        ("아이디/비밀번호: ", "sampleuser Pass123!"),
        (" ID / PW : \t", "sampleuser\tPass123!"),
        ("아이디/비밀번호는 ", "sampleuser와 Pass123!이며"),
        ("아이디/비밀번호 는 ", "sampleuser 와 Pass123!"),
        ("ID/PW: ", "sampleuser/Pass123!"),
        ("ID/PW: ", "sampleuser | Pass123!"),
        ("ID/PW\n", "sampleuser Pass123!"),
    ],
)
def test_explicit_pair_space_delimited_password_does_not_leak_or_erase_facts(
    prefix, values
):
    facts = " 비용은5000원이고삭제예정일은2026-10-07입니다. 계정정보가변경되었습니다."
    result = redact_credentials(prefix + values + facts)
    assert "sampleuser" not in result
    assert "Pass123!" not in result
    assert facts in result
    assert redact_credentials(result) == result


def test_pair_redaction_requires_a_pair_label_and_does_not_consume_missing_password_facts():
    ordinary = "sampleuser Pass123! 비용은5000원입니다."
    assert redact_credentials(ordinary) == ordinary
    result = redact_credentials("ID/PW: sampleuser 비용은5000원입니다.")
    assert "sampleuser" not in result
    assert "비용은5000원입니다." in result
    prose = "아이디/비밀번호는 변경되었습니다. 계정정보가변경되었습니다."
    assert redact_credentials(prose) == prose


@pytest.mark.parametrize(
    "source,credential,service",
    [
        ("628491 is your Example verification code", "628491", "Example"),
        (
            "628491 is your Example Service verification code",
            "628491",
            "Example Service",
        ),
        ("2468은 예시서비스 로그인 코드입니다.", "2468", "예시서비스"),
    ],
)
def test_otp_subject_preserves_service_name_between_code_and_label(
    source, credential, service
):
    safe = redact_credentials(source)
    assert credential not in safe
    assert service in safe
    assert REDACTED in safe
    assert redact_credentials(safe) == safe


def test_connector_redacts_four_digit_code_across_subject_snippet_and_html_body():
    payload = part(
        "<div hidden>HIDDEN-PRIVATE</div><table><tr><td>로그인 코드</td><td>4827</td></tr></table><p>15분간 유효하며 2026년 9월 14일 발급되었습니다.</p>",
        "text/html",
    )
    payload["headers"] += [
        {"name": "Subject", "value": Header("일회성 코드 4827", "utf-8").encode()},
        {"name": "From", "value": "Notice <notice@account.example>"},
    ]
    google = GoogleFixture(
        {
            "id": "message-1",
            "internalDate": "1788000000000",
            "snippet": "로그인 코드 4827",
            "payload": payload,
        }
    )
    connector = GoogleConnector(
        identity=object(),
        google=google,
        oauth_return_url="https://example.invalid/return",
    )
    evidence = connector._message_evidence(
        access_token="synthetic-token", message_id="message-1"
    )
    assert "4827" not in str(evidence)
    assert "HIDDEN-PRIVATE" not in str(evidence)
    assert REDACTED in evidence["title"]
    assert "15분간" in evidence["untrusted_text"]
    assert "2026년 9월 14일" in evidence["untrusted_text"]
    assert "sender_domain=account.example" in evidence["facts"]
