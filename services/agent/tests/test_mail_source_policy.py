from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from quietpilot_agent.mail_source_policy import source_policy
from quietpilot_agent.models import EvidenceRecord


def record(title, body, sender="sender.example"):
    return EvidenceRecord(
        user_id="synthetic-owner-private",
        ref="synthetic-ref-private",
        revision=1,
        source="gmail",
        title=title,
        facts=[f"sender_domain={sender}"],
        untrusted_text=body,
    )


def budget(body=None, sender="costalerts.amazonaws.com"):
    return record(
        "AWS Budgets: forecasted cost alert",
        body
        or "BudgetType: Cost\nBudgetedAmount: $1.00\nAlertType: FORECASTED\nFORECASTEDAmount: $1.15",
        sender,
    )


def test_budget_source_facts_preserve_decimal_precision_without_rounding():
    amount = "123456789012345678901234567890.12"
    policy = source_policy(
        budget(
            f"BudgetType: Cost\nBudgetedAmount: ${amount}\nAlertType: FORECASTED\nFORECASTEDAmount: $1.15"
        ),
        description="",
    )
    assert policy.prompt["source_facts"]["budget_usd"] == amount


def test_registered_deadline_does_not_turn_confirmed_registration_into_a_condition():
    policy = source_policy(deadline(), description="")
    assert policy.summary_issues(
        "행사의 제출 마감 안내로, 사용자가 등록한 경우에 해당해요."
    )
    assert not policy.summary_issues(
        "등록하신 행사의 제출 마감은 메일 발송 당시 3일 남아 있었어요."
    )


@pytest.mark.parametrize("amount", ["1e100", "9" * 65])
def test_non_template_or_unrepresentable_budget_amounts_are_not_forced(amount):
    policy = source_policy(
        budget(
            f"BudgetType: Cost\nBudgetedAmount: ${amount}\nAlertType: FORECASTED\nFORECASTEDAmount: $1.15"
        ),
        description="",
    )
    assert policy.importance_choices == ("HIGH", "NORMAL", "LOW")


def test_scientific_currency_prefix_cannot_satisfy_a_different_source_amount():
    policy = source_policy(budget(), description="")
    assert policy.summary_issues("예상 비용 $1.15가 예산 $1e1USD를 초과할 전망이에요.")


def deadline(body=None):
    return record(
        "Final Call: Future Agents Hackathon",
        body
        or "You signed up for Future Agents Hackathon. Only 3 days left to submit your project.",
    )


def deletion(body=None):
    return record(
        "계정 삭제 예정 안내",
        body
        or "삭제예정일: 2026-10-07. 개인정보와 잔액은 삭제되며 복구할 수 없습니다.",
    )


def test_relative_deadline_example_uses_source_count_without_inventing_a_date():
    policy = source_policy(deadline(), description="")
    example = policy.relative_deadline_example()
    assert example is not None and "'3 days left' when the email was sent" in example
    assert policy.summary_issues(example) == ()
    assert policy.summary_issues("제출 마감은 2026년 9월 15일이에요.")
    assert policy.summary_issues("제출 마감까지 3일 남았어요.")


@pytest.mark.parametrize("phrase", ["3 DAYS REMAINING", "3 days to go"])
def test_relative_example_quotes_the_actual_source_phrase_without_translation(phrase):
    policy = source_policy(
        deadline(
            f"You signed up for Future Agents Hackathon. Only {phrase} to submit."
        ),
        description="",
    )
    example = policy.relative_deadline_example()
    assert example is not None and f"'{phrase}' when the email was sent" in example
    assert policy.summary_issues(example) == ()


def test_english_deadline_and_korean_loss_sources_keep_all_factual_requirements():
    policy = source_policy(deadline(), description="")
    assert not policy.summary_issues(
        "A submission reminder for the registered event. When the email was sent, 3 days remained."
    )
    assert policy.summary_issues("The submission deadline is in 3 days.")
    assert policy.summary_issues("If you registered, this submission deadline applies.")
    loss = source_policy(deletion(), description="")
    assert not loss.summary_issues(
        "The account will be deleted on 2026-10-07. Personal data and the balance will be permanently deleted."
    )
    assert loss.summary_issues("The account will be deleted on 2026-10-07.")
    assert loss.summary_issues(
        "Personal data and the balance will be permanently deleted on 2026-10-08."
    )


@pytest.mark.parametrize(
    "body",
    [
        "You signed up for Future Agents Hackathon. Only 3 days left. Deadline: September 14.",
        "You signed up for Future Agents Hackathon. Only 3 days left. Submit at 8PM EDT.",
        "You signed up for Future Agents Hackathon. Only 3 days left. Earlier notice: 5 days left.",
        "Sign up for Future Agents Hackathon. Only 3 days left to join.",
    ],
)
def test_relative_example_does_not_replace_explicit_or_ambiguous_source_facts(body):
    assert (
        source_policy(deadline(body), description="").relative_deadline_example()
        is None
    )


def test_google_profile_sharing_excludes_only_exact_dataset_labels():
    policy = source_policy(
        record(
            "Your Google Account information was shared with Example App",
            "The shared profile contains your name, photo and email address.",
            "accounts.google.com",
        ),
        description="",
    )
    assert all(
        not policy.tag_allowed(tag)
        for tag in ["데이터셋", "데이터세트", "Dataset", "ＤＡＴＡＳＥＴＳ"]
    )
    assert policy.tag_allowed("데이터셋연구") and policy.tag_allowed("보안알림")
    assert policy.importance_choices == ("HIGH", "NORMAL", "LOW")


@pytest.mark.parametrize(
    "sender,title,body",
    [
        ("google.com.evil.example", "Google Account shared", "name photo email"),
        ("google.com", "Google Account update", "name photo email"),
        ("google.com", "Google Account shared", "name photo"),
    ],
)
def test_google_template_near_misses_are_not_blocked(sender, title, body):
    assert source_policy(record(title, body, sender), description="").tag_allowed(
        "dataset"
    )


def test_structured_aws_forecast_is_high_and_keeps_currency_and_forecast():
    policy = source_policy(budget(), description="")
    assert policy.importance_choices == ("HIGH",)
    assert not policy.tag_allowed("Security-Alert") and not policy.tag_allowed(
        "데이터셋"
    )
    assert policy.summary_issues("예상 비용은 $1.15로 예산 1달러보다 높아요.") == ()
    assert policy.summary_issues("예측 비용 USD 1.150, 예산은 1.0 USD예요.") == ()
    assert any(
        "forecasted" in value
        for value in policy.summary_issues("비용은 $1.15로 예산 $1.00을 넘었어요.")
    )
    assert any(
        "currency" in value
        for value in policy.summary_issues("예상 비용 1.15와 예산 1.00을 확인해요.")
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace("FORECASTED", "ACTUAL"),
        lambda text: text.replace("BudgetType: Cost", "BudgetType: Usage"),
        lambda text: text.replace("FORECASTEDAmount: $1.15", "FORECASTEDAmount: 1.15"),
        lambda text: text + " BudgetedAmount: $9.00",
    ],
)
def test_incomplete_or_conflicting_budget_templates_do_not_force_priority(change):
    policy = source_policy(budget(change(budget().untrusted_text)), description="")
    assert policy.importance_choices == ("HIGH", "NORMAL", "LOW")
    assert policy.tag_allowed("보안알림")


def test_budget_sender_is_exact_and_decimal_thousands_are_supported():
    assert source_policy(
        budget(sender="other.amazonaws.com"), description=""
    ).tag_allowed("dataset")
    policy = source_policy(
        budget(
            budget()
            .untrusted_text.replace("$1.00", "$1,000.00")
            .replace("$1.15", "$1,150.00")
        ),
        description="",
    )
    assert policy.summary_issues("예상 비용은 1150달러이며 예산은 $1000예요.") == ()


def test_registered_named_event_preserves_submission_and_original_countdown_context():
    policy = source_policy(deadline(), description="")
    assert policy.importance_choices == ("HIGH",)
    assert not policy.tag_allowed("보안알림") and not policy.tag_allowed("datasets")
    assert (
        policy.summary_issues(
            "참가한 해커톤의 제출 마감 안내예요. 메일 발송 당시 3일 남았다는 내용이에요."
        )
        == ()
    )
    assert any(
        "current countdown" in value
        for value in policy.summary_issues("제출 마감까지 3일 남았어요.")
    )
    assert any(
        "absolute" in value
        for value in policy.summary_issues("제출 마감은 2026년 9월 16일이에요.")
    )


@pytest.mark.parametrize(
    "summary",
    [
        "행사 참가자에게 제출 기한까지 3일이 남았음을 알려주는 최종 알림입니다.",
        "등록한 행사의 제출 마감까지 3일은 남아 있어요.",
        "등록한 행사의 제출 마감까지 3일밖에 남지 않았어요.",
        "등록한 행사의 제출 마감은 사흘 정도만 남았어요.",
        "등록한 행사의 제출 마감까지 3일을 남겨두고 있어요.",
        "등록한 행사의 제출 기한은 3일이 아직 남았습니다.",
        "등록한 행사의 제출 마감까지 남은 기간은 3일입니다.",
        "등록한 행사의 남아 있는 제출 기간이 사흘이에요.",
        "등록한 행사의 잔여 제출 기간은 3일이에요.",
        "등록한 행사의 남은 제출 기간은 단 3일입니다.",
        "등록한 행사의 잔여 제출 기간은 약 사흘이에요.",
        "등록한 행사의 제출 기한까지는 사흘뿐이에요.",
    ],
)
def test_relative_countdown_particles_and_word_order_require_same_sentence_origin(
    summary,
):
    policy = source_policy(deadline(), description="")
    assert any("current countdown" in issue for issue in policy.summary_issues(summary))
    assert policy.summary_issues("메일 발송 당시 " + summary) == ()
    assert policy.summary_issues(summary.rstrip(".") + " (메일 수신 시점 기준).") == ()
    assert any(
        "current countdown" in issue
        for issue in policy.summary_issues("메일 발송 당시의 안내입니다. " + summary)
    )


@pytest.mark.parametrize(
    "summary",
    [
        "등록한 행사의 제출 준비에는 3일이 필요해요.",
        "등록한 행사는 3일간 진행되며 제출 마감을 안내해요.",
        "등록한 행사의 제출 준비 기간은 3일 정도예요.",
        "등록한 행사의 제출 준비 기간은 지난번보다 3일 더 짧아요.",
        "등록한 행사의 제출 마감은 3일 전과 비교해 바뀌지 않았어요.",
        "등록한 행사의 제출 마감인 2026년 10월 3일 전에 준비를 마쳐요.",
        "등록한 행사의 제출 마감은 2026-10-03이에요.",
    ],
)
def test_relative_countdown_does_not_reject_durations_comparisons_or_source_dates(
    summary,
):
    policy = source_policy(
        deadline(deadline().untrusted_text + " Submission deadline: 2026-10-03."),
        description="",
    )
    assert policy.summary_issues(summary) == ()
    assert any(
        "absolute" in value
        for value in policy.summary_issues("제출 마감은 9월 16일이에요.")
    )
    assert any(
        "reminder" in value
        for value in policy.summary_issues("행사의 일반 소식이에요.")
    )


@pytest.mark.parametrize(
    "body",
    [
        "You signed up for Other Agents Hackathon. Only 3 days left.",
        "You registered for this event. Only 3 days left.",
        "If you registered for Future Agents Hackathon, submit soon.",
        "You signed up for our newsletter about Future Agents Hackathon.",
    ],
)
def test_registration_needs_unconditional_same_named_event(body):
    assert source_policy(deadline(body), description="").importance_choices == (
        "HIGH",
        "NORMAL",
        "LOW",
    )


def test_source_absolute_submission_date_can_be_reexpressed_in_korean():
    policy = source_policy(
        deadline(
            "You signed up for Future Agents Hackathon. Submission deadline: 2026-10-03."
        ),
        description="",
    )
    assert (
        policy.summary_issues("신청한 대회의 제출 마감은 2026년 10월 3일이에요.") == ()
    )


def test_registered_event_name_must_end_at_a_title_word_boundary():
    source = record(
        "Final Call: Future Agents HackathonX",
        "You signed up for Future Agents Hackathon. Only 3 days left.",
    )
    assert source_policy(source, description="").importance_choices == (
        "HIGH",
        "NORMAL",
        "LOW",
    )


def test_recording_title_with_explicit_registered_submission_cutoff_is_high():
    policy = source_policy(
        record(
            "[Recording Available] Future Agents Hackathon",
            "You signed up for Future Agents Hackathon. 5 days left — a few tips. "
            "Start your submission draft now. Sep 14, 8PM EDT is a hard cutoff.",
        ),
        description="",
    )
    assert policy.importance_choices == ("HIGH",)
    assert (
        "registered_event_submission_reminder" in policy.prompt["recognized_templates"]
    )
    assert (
        policy.summary_issues("등록한 행사의 제출 마감은 9월 14일 오후 8시 EDT예요.")
        == ()
    )
    assert any(
        "current countdown" in issue
        for issue in policy.summary_issues("등록한 행사의 제출 마감까지 5일 남았어요.")
    )


@pytest.mark.parametrize(
    "source_date,korean",
    [
        ("Sep 14", "9월 14일"),
        ("Sept. 14", "9월 14일"),
        ("September 14", "9월 14일"),
        ("October 3", "10월 3일"),
        ("Dec 31", "12월 31일"),
        ("September 14, 2026", "2026년 9월 14일"),
        ("September 14,2026", "2026년 9월 14일"),
    ],
)
def test_english_submission_month_day_translates_without_guessing_year(
    source_date, korean
):
    policy = source_policy(
        deadline(
            f"You signed up for Future Agents Hackathon. Submission deadline: {source_date}."
        ),
        description="",
    )
    assert policy.summary_issues(f"등록한 행사의 제출 마감은 {korean}이에요.") == ()
    assert any(
        "absolute" in issue
        for issue in policy.summary_issues("제출 마감은 9월 15일이에요.")
    )
    if "2026" not in source_date:
        assert any(
            "absolute" in issue
            for issue in policy.summary_issues("제출 마감은 2026년 9월 14일이에요.")
        )


@pytest.mark.parametrize(
    "body",
    [
        "You signed up for Future Agents Hackathon. The recording is available. Start your submission draft now.",
        "You signed up for Future Agents Hackathon. Submission deadline will be announced. Recording available Sep 14.",
        "You signed up for Future Agents Hackathon. Submission deadline is not Sep 14; the date is undecided.",
        "If you signed up for Future Agents Hackathon, the submission deadline is Sep 14.",
        "You signed up for our newsletter about Future Agents Hackathon. The submission deadline is Sep 14.",
        "The submission deadline is Sep 14. Sign up for Future Agents Hackathon now.",
        "You signed up for Future Agents Hackathon. There is no hard cutoff for submissions. Recording available Sep 14.",
        "You signed up for Future Agents Hackathon. Submission deadline: Sep 31.",
    ],
)
def test_recording_body_needs_a_real_registration_and_unambiguous_submission_cutoff(
    body,
):
    policy = source_policy(
        record("[Recording Available] Future Agents Hackathon", body), description=""
    )
    assert policy.importance_choices == ("HIGH", "NORMAL", "LOW")


def test_event_newsletter_signup_does_not_become_personal_registration():
    policy = source_policy(
        record(
            "[Recording Available] Future Agents Hackathon newsletter",
            "You signed up for Future Agents Hackathon newsletter. Submission deadline: Sep 14.",
        ),
        description="",
    )
    assert policy.importance_choices != ("HIGH",)


def test_registered_cutoff_cannot_add_unstated_clock_or_timezone():
    no_clock = source_policy(
        deadline(
            "You signed up for Future Agents Hackathon. Submission deadline: Sep 14."
        ),
        description="",
    )
    assert any(
        "time" in issue
        for issue in no_clock.summary_issues("제출 마감은 9월 14일 오후 8시예요.")
    )
    with_clock = source_policy(
        deadline(
            "You signed up for Future Agents Hackathon. Submission deadline: Sep 14, 8PM EDT."
        ),
        description="",
    )
    assert with_clock.summary_issues("제출 마감은 9월 14일 20:00 EDT예요.") == ()
    assert any(
        "time zone" in issue
        for issue in with_clock.summary_issues("제출 마감은 9월 14일 오후 8시 KST예요.")
    )
    assert any(
        "time" in issue
        for issue in with_clock.summary_issues("제출 마감은 9월 14일 오후 9시 EDT예요.")
    )


def test_body_submit_by_deadline_preserves_custom_description_priority_authority():
    source = record(
        "Recording: Future Agents Hackathon",
        "You registered for Future Agents Hackathon. Submit your project by Sep 14.",
    )
    assert source_policy(source, description="").importance_choices == ("HIGH",)
    policy = source_policy(source, description="등록한 대회 소식을 보고 싶어요.")
    assert policy.importance_choices == ("HIGH", "NORMAL", "LOW")
    assert policy.summary_issues("제출 마감은 9월 14일이에요.") == ()


def test_known_utc_receipt_date_requires_explicit_metadata_attribution():
    source = deadline()
    milliseconds = int(datetime(2026, 9, 12, 18, tzinfo=UTC).timestamp()) * 1000
    source.facts.append(f"received_at_unix_ms={milliseconds}")
    policy = source_policy(source, description="")
    assert policy.prompt["source_facts"]["received_date_utc"] == "2026-09-12"
    assert (
        policy.summary_issues(
            "2026년 9월 12일 받은 메일은 발송 당시 3일 남았다고 알려주는 제출 마감 안내예요."
        )
        == ()
    )
    assert (
        policy.summary_issues(
            "메일 수신일은 2026-09-12예요. 발송 당시 마감까지 3일 남았다는 제출 안내예요."
        )
        == ()
    )
    for summary in [
        "제출 마감은 2026년 9월 12일이에요.",
        "2026년 9월 12일 받은 메일이에요. 제출 마감은 2026년 9월 12일이에요.",
        "2026년 9월 13일 받은 메일이 제출 마감을 알려줘요.",
    ]:
        assert any("absolute" in issue for issue in policy.summary_issues(summary))


@pytest.mark.parametrize("epoch", ["not-a-time", "-1", "9999999999999999"])
def test_invalid_receipt_epoch_does_not_authorize_a_metadata_date(epoch):
    source = deadline()
    source.facts.append(f"received_at_unix_ms={epoch}")
    policy = source_policy(source, description="")
    assert "received_date_utc" not in policy.prompt["source_facts"]
    assert any(
        "absolute" in issue
        for issue in policy.summary_issues(
            "2026년 9월 12일 받은 메일이 제출 마감을 알려줘요."
        )
    )


def test_account_deletion_requires_date_and_explicit_irreversible_losses():
    policy = source_policy(deletion(), description="")
    assert policy.importance_choices == ("HIGH",)
    assert (
        policy.summary_issues(
            "계정이 2026년 10월 7일에 삭제될 예정이에요. 개인정보와 잔액이 삭제되며 복구할 수 없어요."
        )
        == ()
    )
    assert (
        policy.summary_issues("2026-10-07에 개인정보와 잔액이 영구적으로 삭제돼요.")
        == ()
    )
    issues = policy.summary_issues("계정 삭제를 확인해 주세요.")
    assert len(issues) == 4
    assert any(
        "scheduled deletion date" in value
        for value in policy.summary_issues(
            "2026-10-08에 개인정보와 잔액이 영구 삭제돼요."
        )
    )


@pytest.mark.parametrize(
    "title,body",
    [
        ("파일 삭제 안내", "삭제예정일: 2026-10-07"),
        ("계정 삭제 안내", "삭제예정일: 2026-13-07"),
        ("계정 삭제 안내", "삭제예정일: 2026-10-07 삭제예정일: 2026-10-08"),
    ],
)
def test_deletion_near_misses_do_not_force_priority(title, body):
    assert source_policy(record(title, body), description="").importance_choices == (
        "HIGH",
        "NORMAL",
        "LOW",
    )


def test_deletion_does_not_invent_unstated_loss():
    policy = source_policy(
        deletion("삭제예정일: 2026-10-07. 자세한 정보는 안내를 확인해 주세요."),
        description="",
    )
    assert policy.summary_issues("계정은 2026-10-07에 삭제될 예정이에요.") == ()


@pytest.mark.parametrize(
    "title",
    [
        "Call for Speakers: Applied AI Conference",
        "Call for Papers",
        "발표자 모집",
        "서포터즈 모집",
    ],
)
def test_generic_outreach_is_at_most_normal_without_security_or_unrelated_dataset(
    title,
):
    policy = source_policy(
        record(title, "Join our event and submit a proposal."), description=""
    )
    assert policy.importance_choices == ("NORMAL", "LOW")
    assert not policy.tag_allowed("보안알림") and not policy.tag_allowed("데이터셋")


def test_outreach_keeps_specific_dataset_topics_and_personal_participation_exception():
    policy = source_policy(
        record(
            "Call for Papers",
            "We welcome studies about Parquet datasets and training data.",
        ),
        description="",
    )
    assert policy.tag_allowed("데이터셋") and policy.importance_choices == (
        "NORMAL",
        "LOW",
    )
    personal = source_policy(
        record("Call for Speakers", "Your speaker proposal was accepted."),
        description="",
    )
    assert personal.importance_choices == ("HIGH", "NORMAL", "LOW")
    newsletter = source_policy(
        record("Call for Speakers", "You signed up for our newsletter."), description=""
    )
    assert newsletter.importance_choices == ("NORMAL", "LOW")


@pytest.mark.parametrize(
    "title",
    ["발표자를 모집합니다", "서포터즈 1기 모집", "서포터즈 제 12기를 모집합니다"],
)
def test_korean_outreach_particles_and_cohort_numbers(title):
    policy = source_policy(
        record(title, "관심 있는 분은 지원해 주세요."), description=""
    )
    assert policy.importance_choices == ("NORMAL", "LOW")
    assert not policy.tag_allowed("보안알림")


@pytest.mark.parametrize(
    "title,body",
    [
        (
            "서포터즈 1기 모집 사칭 피싱 주의",
            "이 모집 메일을 사칭한 피싱에 주의하세요.",
        ),
        (
            "보안 경고: 발표자를 모집합니다",
            "모집 안내를 사칭한 이메일이 유포되고 있어요.",
        ),
        ("Call for Papers - phishing warning", "Beware of phishing invitations."),
        ("서포터즈 1기 모집", "이 제목의 메일을 사칭한 피싱에 주의하세요."),
    ],
)
def test_quoted_recruitment_in_security_warning_is_not_treated_as_outreach(title, body):
    policy = source_policy(record(title, body), description="")
    assert policy.importance_choices == ("HIGH", "NORMAL", "LOW")
    assert policy.tag_allowed("보안알림")


def test_useful_dataset_notification_remains_unconstrained():
    policy = source_policy(
        record(
            "Dataset converted to Parquet",
            "A Parquet version of your dataset is available. No action is required.",
        ),
        description="",
    )
    assert policy.tag_allowed("데이터셋") and policy.importance_choices == (
        "HIGH",
        "NORMAL",
        "LOW",
    )
    assert policy.summary_issues("데이터셋의 Parquet 변환본을 사용할 수 있어요.") == ()


@pytest.mark.parametrize(
    "source",
    [budget(), deadline(), deletion(), record("Call for Speakers", "Join our event.")],
)
def test_custom_description_removes_priority_and_tag_constraints_but_keeps_copy_checks(
    source,
):
    policy = source_policy(source, description="사용자 정의 관심 설명")
    assert policy.importance_choices == ("HIGH", "NORMAL", "LOW")
    assert policy.tag_allowed("보안알림") and policy.tag_allowed("데이터셋")
    if source.title != "Call for Speakers":
        assert policy.summary_issues("이 소식을 확인해 주세요.")


def test_third_party_app_is_not_an_ordinal_app_even_with_custom_description():
    source = record(
        "Application authorized",
        "A third-party OAuth application has read-only access.",
    )
    policy = source_policy(source, description="내 계정 소식")
    assert policy.summary_issues("세 번째 OAuth 앱에 접근 권한이 생겼어요.")
    assert policy.summary_issues("세번째애플리케이션을 확인해요.")
    assert policy.summary_issues("외부 OAuth 앱의 접근 권한을 확인해요.") == ()
    ordinal = source_policy(
        record(
            source.title,
            source.untrusted_text + " This is the third OAuth application.",
        ),
        description="",
    )
    assert ordinal.summary_issues("세 번째 OAuth 앱을 확인해요.") == ()


def test_prompt_and_feedback_do_not_expose_owner_identifiers_or_custom_text():
    policy = source_policy(budget(), description="PRIVATE-DESCRIPTION")
    encoded = json.dumps(policy.prompt, ensure_ascii=False)
    assert (
        "synthetic-owner-private" not in encoded
        and "synthetic-ref-private" not in encoded
    )
    assert "PRIVATE-DESCRIPTION" not in encoded
    feedback = " ".join(policy.summary_issues("확인해 주세요."))
    assert "1.15" not in feedback and "1.00" not in feedback
