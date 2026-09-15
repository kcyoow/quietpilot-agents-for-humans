from __future__ import annotations

import json

import pytest
from quietpilot_agent.mail_source_policy import source_policy
from quietpilot_agent.models import EvidenceRecord

ALL = ("HIGH", "NORMAL", "LOW")
AUTH_REQUESTS = [
    (
        "628491 is your Example verification code",
        "Confirm your email address. You can use this code as a backup, just in case.",
    ),
    (
        "Your sign-in code",
        "Enter this code to sign in. It expires in 10 minutes. If you did not request this, review recent activity.",
    ),
    (
        "Your login link",
        "Use this link to log in. If your account was compromised, review account activity.",
    ),
    ("Your security code", "This code is valid for 10 minutes. Enter it to sign in."),
    ("Verify your email", "Click the link to confirm your email address."),
    ("이메일 주소 인증", "이메일 주소 인증을 위해 아래 링크를 클릭하세요."),
    (
        "Password reset request",
        "You requested a password reset. Click the link to reset your password. If you did not request this, ignore this email.",
    ),
    (
        "비밀번호 재설정 요청",
        "비밀번호 재설정 요청에 따라 링크를 보내드립니다. 요청하지 않았다면 무시하세요. 계정이 침해되었다면 활동을 확인하세요.",
    ),
    ("로그인 인증 코드", "로그인 인증 코드를 입력하세요. 코드는 10분 후 만료됩니다."),
    (
        "How to reset your Sample Workspace password",
        "Click the button to reset your password. If you did not request this, ignore this email.",
    ),
    (
        "비밀번호를 재설정해 주세요",
        "비밀번호 재설정 요청에 따라 안내드립니다. 요청하지 않았다면 접속 기록을 확인하세요. 아래 버튼으로 비밀번호를 재설정하세요.",
    ),
    (
        "Sign in to Sample Link",
        "Click the button to sign in. This link will expire in 15 minutes. Ignore this email if you did not request it.",
    ),
    (
        "Confirm your email address",
        "Confirm your email address. Confirm [redacted URL]. You can use this code as a backup.",
    ),
    (
        "Verify your identity",
        "Use this verification code to confirm your account. 코드를 입력해 계정 확인을 완료하세요.",
    ),
    (
        "온라인 서비스 비밀번호 지원",
        "이 이메일 주소와 연결된 계정의 비밀번호 재설정 요청을 받았습니다. 아래 링크에서 재설정하십시오. 요청하지 않은 경우 이메일을 무시하세요. 계정은 안전합니다.",
    ),
    (
        "Password assistance",
        "We received a request to reset your password. Follow the link. If you did not request this, ignore the email.",
    ),
    (
        "How to reset your Sample password",
        "You requested a password reset. If your Sample password has changed unexpectedly, review recent activity.",
    ),
]
GENERAL = [
    (
        "Security newsletter",
        "This week: research on reward hacking and model security. Unsubscribe below.",
    ),
    (
        "Model research digest",
        "Latest research papers on machine learning and model alignment.",
    ),
    ("AI roundup", "This edition covers reward hacking and new model papers."),
    (
        "시큐리티레터",
        "이번 호는 보상 해킹 연구와 최신 보안 논문 소식입니다. 구독을 해지할 수 있습니다.",
    ),
    ("새로운 소식", "이번 주 최신 연구 논문과 소식을 공유합니다."),
    ("업데이트 공유", "최신 연구와 모델 업데이트 모음을 전합니다."),
    (
        "연구 모임님이 업데이트를 공유함: 모델 논문 소식",
        "게시물을 공유함. 업데이트 보기. 이 알림 이메일을 받고 있습니다.",
    ),
    (
        "[모임] 새로운 소식이 도착했습니다!",
        "A brief summary of community discussions. 새 주제와 인기 주제를 확인하세요.",
    ),
    (
        "보안 제품 특별 할인",
        "구매 시 할인 쿠폰을 사용할 수 있습니다. 보안 알림 기능도 제공합니다.",
    ),
    (
        "Security software special offer",
        "Buy now with a discount on account protection and security alerts.",
    ),
    (
        "알림서비스 이용약관 변경 안내",
        "이용약관이 개정되어 다음 달부터 시행됩니다. 알림 서비스의 보안 관련 조항을 확인하세요.",
    ),
    (
        "Terms of service update",
        "Our terms are updated and take effect next month. Security policy wording has changed.",
    ),
]
OUTREACH = [
    ("공개 특강 참가 신청 안내", "관심 있는 누구나 아래 링크로 참가 신청하세요."),
    ("멘토링 초대", "멘토링 참여를 원하시면 등록하세요."),
    ("보안 세미나 초대", "공개 보안 세미나에 참여하실 분은 신청하세요."),
    ("Join our public webinar", "You are invited to register for this webinar."),
    (
        "[학습 센터] 학습법 특강 안내 (온라인)",
        "신청 링크에서 접수하세요. 대상: 관심 있는 학생 누구나. 많은 참여 바랍니다.",
    ),
    (
        "정보보호 분야 소통 DAY 개최",
        "멘토링 기회를 제공합니다. 신청 링크를 확인하고 많은 참여 바랍니다.",
    ),
]


def _record(title, body):
    return EvidenceRecord(
        user_id="synthetic-owner",
        ref="synthetic-source",
        revision=1,
        source="gmail",
        title=title,
        untrusted_text=body,
    )


@pytest.mark.parametrize("title,body", AUTH_REQUESTS)
def test_explicit_transient_auth_requests_are_low_despite_conditional_safety_footer(
    title, body
):
    policy = source_policy(_record(title, body), description="")
    assert policy.importance_choices == ("LOW",)
    assert "transient_authentication_request" in policy.prompt["recognized_templates"]
    assert not policy.tag_allowed("Security Alert")


@pytest.mark.parametrize(
    "title,body",
    [
        (
            "Reset your password",
            "We detected unusual sign-in to your account. Click the link to reset your password.",
        ),
        (
            "Unusual sign-in detected: reset your password",
            "Click the link to reset your password.",
        ),
        (
            "비밀번호 재설정 필요",
            "회원님의 비밀번호가 유출되었습니다. 비밀번호 재설정 링크를 사용하세요.",
        ),
        (
            "비밀번호 재설정 필요",
            "비정상 접속이 감지되었습니다. 비밀번호 재설정 링크를 사용하세요.",
        ),
        (
            "Password reset completed",
            "Your password has changed. Use the link to reset your password if needed.",
        ),
        (
            "비밀번호가 변경됨",
            "비밀번호 변경이 완료되었습니다. 비밀번호 재설정 링크도 제공됩니다.",
        ),
        ("Your login code", ""),
        (
            "Verification code research",
            "This paper discusses authentication protocols and security.",
        ),
        (
            "Service information",
            "Enter this code to sign in. This code expires in 10 minutes.",
        ),
        (
            "How to reset your Sample Workspace password",
            "Your Sample Workspace password has been changed. Click the button to reset your password if needed.",
        ),
        (
            "How to reset your Sample password",
            "Your Sample password has been compromised. Click the button to reset your password.",
        ),
        ("Verify your identity", "Upload your identity documents for manual review."),
        (
            "비밀번호 지원",
            "비밀번호를 잊었을 때 재설정하는 방법을 설명하는 도움말 문서입니다.",
        ),
        (
            "Password assistance",
            "If you received a request to reset your password, follow the link. This article explains password support.",
        ),
        (
            "비밀번호 지원",
            "비밀번호가 유출되었습니다. 비밀번호 재설정 요청을 받았습니다.",
        ),
    ],
)
def test_auth_requests_need_both_sources_and_do_not_hide_completed_or_detected_events(
    title, body
):
    policy = source_policy(_record(title, body), description="")
    assert policy.importance_choices == ALL
    assert policy.tag_allowed("보안알림")


@pytest.mark.parametrize("title,body", GENERAL + OUTREACH)
def test_explicit_general_information_has_no_personal_security_or_dataset_claim(
    title, body
):
    policy = source_policy(_record(title, body), description="")
    assert policy.importance_choices == ("NORMAL", "LOW")
    assert not policy.tag_allowed("보안 알림")
    assert not policy.tag_allowed("Dataset")


@pytest.mark.parametrize(
    "content",
    ["New dataset release", "A new corpus in Parquet", "학습용 데이터셋 공개"],
)
def test_digest_with_explicit_dataset_source_preserves_dataset_interest(content):
    policy = source_policy(
        _record("Research newsletter", f"This week: {content}. Subscribe for updates."),
        description="",
    )
    assert policy.tag_allowed("데이터셋")
    assert not policy.tag_allowed("보안알림")


@pytest.mark.parametrize(
    "title,body",
    [
        (
            "Security newsletter",
            "This week we detected unusual access to your account. Reset your password.",
        ),
        ("보안 세미나 초대", "신청하신 보안 세미나의 참가 일정 안내입니다."),
        (
            "공개 특강 참가 신청 안내",
            "참가 신청이 확정되었습니다. 등록하신 일정에 참석하세요.",
        ),
        (
            "Webinar registration",
            "Your registration has been confirmed. Your event is tomorrow.",
        ),
        (
            "서포터즈 모집 사칭 피싱 주의",
            "비정상 접속이 감지되었습니다. 계정 보안 경고입니다.",
        ),
        (
            "Terms of service update",
            "Our terms are updated. Your account has been compromised. Reset your password.",
        ),
        (
            "Security software special offer",
            "Buy now. Your password was found in a data breach. Reset your password.",
        ),
    ],
)
def test_personal_confirmation_or_account_incident_is_never_capped_by_generic_topic(
    title, body
):
    policy = source_policy(_record(title, body), description="")
    assert policy.importance_choices == ALL
    assert policy.tag_allowed("보안알림")


def test_personal_obligation_in_digest_keeps_model_priority_choice():
    policy = source_policy(
        _record(
            "Monthly digest",
            "This month: your invoice is due tomorrow. Latest news follows.",
        ),
        description="",
    )
    assert policy.importance_choices == ALL
    assert not policy.tag_allowed("보안알림")


@pytest.mark.parametrize(
    "title,body",
    [
        ("새로운 소식", "본문을 읽을 수 없습니다."),
        ("Your account update", "Your account settings have changed."),
        ("Service notification", "The terms are updated and take effect tomorrow."),
        ("보안 세미나 초대", "본문을 읽을 수 없습니다."),
        ("연구 모임님이 업데이트를 공유함", "본문을 읽을 수 없습니다."),
        ("[센터] 학습법 특강 안내", "본문을 읽을 수 없습니다."),
        (
            "Your AI tools can now work directly with cloud storage",
            "A new server is available as open source. Try it today with security safeguards.",
        ),
        (
            "What to read before the technology event",
            "Explore sessions and speakers, learn more, and build your schedule.",
        ),
    ],
)
def test_ambiguous_or_unreadable_source_keeps_model_decision(title, body):
    policy = source_policy(_record(title, body), description="")
    assert policy.importance_choices == ALL
    assert policy.tag_allowed("보안알림") and policy.tag_allowed("Dataset")


@pytest.mark.parametrize("title,body", AUTH_REQUESTS + GENERAL + OUTREACH)
def test_custom_description_retains_relevance_and_priority_authority(title, body):
    policy = source_policy(
        _record(title, body), description="인증 메일과 연구 및 행사 안내도 받고 싶어요."
    )
    assert policy.importance_choices == ALL
    assert policy.tag_allowed("보안알림") and policy.tag_allowed("Dataset")
    assert policy.prompt["custom_description_controls_relevance"] is True


def test_new_policy_prompt_contains_only_static_classification_not_mail_payload():
    marker = "SYNTHETIC_PRIVATE_PAYLOAD_42"
    policy = source_policy(
        _record("Model digest", f"Latest research papers. {marker}"), description=""
    )
    assert marker not in json.dumps(policy.prompt)


@pytest.mark.parametrize(
    "link",
    [
        "https://auth.example.test/history",
        "https://auth.example.test/history?next=account#activity",
    ],
)
def test_link_dots_do_not_turn_conditional_security_advice_into_an_incident(link):
    policy = source_policy(
        _record(
            "비밀번호를 재설정해 주세요",
            "비밀번호 재설정 요청을 받았습니다. 요청하지 않으신 경우 최근 접속 기록 "
            + link
            + " 를 보고 의심스러운 로그인 기록이 있는지 확인하세요.",
        ),
        description="",
    )
    assert policy.importance_choices == ("LOW",)


def test_check_for_suspicious_activity_is_not_a_report_it_was_detected():
    policy = source_policy(
        _record(
            "Password reset request",
            "Click the button to reset your password. 의심스러운 로그인 기록이 있는지 확인하세요.",
        ),
        description="",
    )
    assert policy.importance_choices == ("LOW",)


def test_real_incident_after_a_link_sentence_remains_eligible():
    policy = source_policy(
        _record(
            "Reset your password",
            "Help is at https://help.example.test/account. Unusual sign-in detected. Click the link to reset your password.",
        ),
        description="",
    )
    assert policy.importance_choices == ALL


# S053 metadata plus the independently checked program/CTA facts from its body.
# The classifier rule must not depend on this title, company, or venue.
EVENT_PREVIEW_TITLE = "What to read before San Jose"
EVENT_PREVIEW = (
    "Agent horror stories, how to keep your secrets safe, and a real supply chain attack. "
    "Here's a sneak peek. Docker x WAD docker run docker/next. "
    "These three sessions introduce security case studies. Learn More. "
    "Build your schedule around these, then catch the speakers afterward to learn more. "
    "Event details: World Congress."
)


@pytest.mark.parametrize(
    "title,body",
    [
        (EVENT_PREVIEW_TITLE, EVENT_PREVIEW),
        (
            "A look ahead to our next gathering",
            (
                "Conference sessions cover protecting secrets and an attack case study. "
                "Meet the speakers and learn more. Here's a sneak peek at the program."
            ),
        ),
        (
            "개발자들의 다음 만남",
            (
                "행사 미리보기입니다. 보안 사고 사례를 다루는 세션과 강연을 소개합니다. "
                "발표자와 연사를 만나보세요. 자세히 보기에서 프로그램을 확인하세요."
            ),
        ),
    ],
)
def test_explicit_educational_event_promotion_is_not_a_personal_security_alert(
    title, body
):
    policy = source_policy(_record(title, body), description="")
    assert "educational_event_promotion" in policy.prompt["recognized_templates"]
    assert not policy.tag_allowed("보안알림")
    assert not policy.tag_allowed("데이터셋")
    assert policy.importance_choices == ("NORMAL", "LOW")


@pytest.mark.parametrize(
    "body",
    [
        "Agent horror stories, how to keep your secrets safe, and a real supply chain attack. Here's a sneak peek.",
        "Conference overview. Meet the speakers and learn more. Here's a sneak peek.",
        "Here's a sneak peek at conference sessions. Learn more about security techniques.",
        "Here's a sneak peek at conference sessions and speakers discussing security.",
        "Sessions and speakers discuss security techniques. Learn more about their work.",
    ],
)
def test_event_words_or_preview_alone_do_not_force_exclusion(body):
    policy = source_policy(_record(EVENT_PREVIEW_TITLE, body), description="")
    assert policy.tag_allowed("보안알림")
    assert policy.importance_choices == ALL
    assert "educational_event_promotion" not in policy.prompt.get(
        "recognized_templates", []
    )


@pytest.mark.parametrize(
    "notice",
    [
        "We detected unusual access to your account.",
        "Your password has been changed.",
        "Your registration has been confirmed.",
        "You have registered for the security conference. Your submission is due tomorrow.",
        "Your assignment is due Friday.",
        "You are required to complete security training by Friday.",
        "신청하신 세미나의 참가 일정입니다.",
        "귀하는 금요일까지 필수 보안 교육을 이수해야 합니다.",
    ],
)
def test_event_promotion_does_not_hide_an_actual_account_event_or_recipient_obligation(
    notice,
):
    policy = source_policy(
        _record(EVENT_PREVIEW_TITLE, EVENT_PREVIEW + " " + notice), description=""
    )
    assert policy.importance_choices == ALL
    assert policy.tag_allowed("보안알림")


def test_event_promotion_preserves_explicit_dataset_news_and_its_priority_choices():
    policy = source_policy(
        _record(
            EVENT_PREVIEW_TITLE,
            EVENT_PREVIEW + " A new dataset has been released with corpus downloads.",
        ),
        description="",
    )
    assert policy.tag_allowed("데이터셋")
    assert not policy.tag_allowed("보안알림")
    assert policy.importance_choices == ALL


def test_event_promotion_keeps_custom_interest_authority_and_no_source_text_in_prompt():
    private_marker = "SYNTHETIC_PREVIEW_COPY_ONLY"
    policy = source_policy(
        _record(EVENT_PREVIEW_TITLE, EVENT_PREVIEW + " " + private_marker),
        description="보안 기술을 설명하는 행사 세션과 교육 미리보기도 보고 싶어요.",
    )
    assert policy.tag_allowed("보안알림") and policy.tag_allowed("데이터셋")
    assert policy.importance_choices == ALL
    assert policy.prompt["custom_description_controls_relevance"] is True
    assert private_marker not in json.dumps(policy.prompt)
