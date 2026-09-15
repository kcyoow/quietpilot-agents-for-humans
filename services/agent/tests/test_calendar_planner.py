from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from quietpilot_agent import InMemoryContextRepository
from quietpilot_agent.calendar_planner import (
    DEADLINE_BLOCK_DESCRIPTION,
    CalendarDraftOutputError,
    build_calendar_draft,
)
from quietpilot_agent.context import ContextAccessDenied
from quietpilot_agent.local_model import DeterministicModel, ModelPlan, ToolStep
from quietpilot_agent.models import CaseType, EvidenceRecord, OrchestrationRequest, Risk

NOW = datetime(2026, 9, 14, tzinfo=UTC)
RECEIVED = datetime(2026, 9, 12, 10, tzinfo=UTC)
BODY = "신청하신 연구 미팅의 예약이 확정되었습니다. 시작 2026-09-20T09:00:00+09:00, 종료 2026-09-20T10:00:00+09:00입니다."


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr("quietpilot_agent.calendar_planner.datetime", Clock)


def inputs(body=BODY, *, direct=False, facts=None, count=1):
    records = [
        EvidenceRecord(
            user_id="synthetic-owner",
            ref=f"private-owned-mail-{index}",
            revision=1,
            source="direct" if direct else "gmail",
            title="연구 미팅 준비",
            facts=facts
            if facts is not None
            else [
                f"received_at_unix_ms={int(RECEIVED.timestamp() * 1000)}",
                "source_content=body",
            ],
            untrusted_text=body,
        )
        for index in range(count)
    ]
    request = OrchestrationRequest(
        user_id=records[0].user_id,
        case_type=CaseType.DIRECT_DELEGATION if direct else CaseType.CONNECTED_SIGNAL,
        goal="연구 미팅 일정을 준비해 줘.",
        evidence_refs=[record.ref for record in records],
        capability_ids=[],
        primary_group_hint="appointments",
        risk=Risk.LOW,
    )
    scope = InMemoryContextRepository(evidence=records, capabilities=[]).open_scope(
        request
    )
    return request, scope


def proposal(**changes):
    return {
        "decision": "PROPOSE",
        "event_kind": "TIMED_EVENT",
        "source_ref": "m1",
        "summary": "Research meeting",
        "start": "2026-09-20T09:00:00+09:00",
        "end": "2026-09-20T10:00:00+09:00",
        "time_zone": "",
        "description": "",
        "start_quote_ref": "m1p1",
        "end_quote_ref": "m1p1",
        "purpose_quote_ref": "m1p1",
        "reason": "원문에 확정된 미팅의 시작과 종료가 명시되어 있어요.",
        **changes,
    }


def abstain():
    return {
        **{key: "" for key in proposal()},
        "decision": "ABSTAIN",
        "event_kind": "NONE",
        "reason": "확실하고 필요한 일정이 없어 준비하지 않아요.",
    }


class SchemaModel(DeterministicModel):
    async def stream(self, messages, tool_specs=None, *args, **kwargs):
        self.schema = tool_specs[0]["inputSchema"]["json"]
        async for event in super().stream(messages, tool_specs, *args, **kwargs):
            yield event


class Factory:
    def __init__(self, draft=None, reviewed=None):
        self.outputs = {
            "calendar_draft_planner": proposal() if draft is None else draft,
            "calendar_draft_verifier": proposal() if reviewed is None else reviewed,
        }
        self.models = {}

    def create(self, role, plan):
        payload = self.outputs[role]
        steps = payload if isinstance(payload, list) else [payload, payload]
        model = SchemaModel(
            role,
            ModelPlan(
                steps=tuple(
                    ToolStep("CalendarDraftAssessment", value) for value in steps
                ),
                output=plan.output,
            ),
        )
        self.models[role] = model
        return model


def test_real_sdk_uses_two_fresh_assessments_and_returns_only_normalized_event_fields():
    request, scope = inputs()
    factory = Factory()
    result = build_calendar_draft(request, scope, factory)
    assert result == {
        "summary": "Research meeting",
        "start": proposal()["start"],
        "end": proposal()["end"],
        "source_ref": request.evidence_refs[0],
    }
    assert list(factory.models) == ["calendar_draft_planner", "calendar_draft_verifier"]
    prompts = []
    for model in factory.models.values():
        assert model.stream_calls == 1
        assert model.tool_calls == ["CalendarDraftAssessment"]
        assert set(model.schema["required"]) == set(proposal())
        assert all(
            value["type"] == "string" for value in model.schema["properties"].values()
        )
        prompts.append(json.loads(model.received_messages[0][0]["content"][0]["text"]))
    assert prompts[0]["sources"] == prompts[1]["sources"]
    assert prompts[0]["sources"][0]["datetime_format_hints"] == [
        {"quote_ref": "m1p1", "rfc3339": proposal()["start"], "time_zone": ""},
        {"quote_ref": "m1p1", "rfc3339": proposal()["end"], "time_zone": ""},
    ]
    assert prompts[1]["draft"]["start_quote_ref"] == "m1p1"
    assert request.user_id not in json.dumps(prompts)
    assert request.evidence_refs[0] not in json.dumps(prompts)
    assert BODY not in json.dumps(result, ensure_ascii=False)
    assert not set(result).intersection(
        {"reason", "start_quote_ref", "end_quote_ref", "purpose_quote_ref"}
    )


@pytest.mark.parametrize(
    "draft,reviewed,selected",
    [(proposal(), abstain(), False), (abstain(), proposal(), True)],
)
def test_fresh_verifier_can_reverse_proposal_or_abstention(draft, reviewed, selected):
    request, scope = inputs()
    result = build_calendar_draft(request, scope, Factory(draft, reviewed))
    assert (result is not None) is selected


def test_malformed_first_output_repairs_within_two_attempts_without_fallback_data():
    request, scope = inputs()
    invalid = proposal(end="2026-09-20T11:00:00+09:00")
    factory = Factory([invalid, proposal()], proposal())
    result = build_calendar_draft(request, scope, factory)
    assert result["end"] == proposal()["end"]
    assert factory.models["calendar_draft_planner"].stream_calls == 2
    assert factory.models["calendar_draft_verifier"].stream_calls == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"source_ref": "outside"},
        {"start_quote_ref": "m2p1"},
        {"purpose_quote_ref": ""},
        {"start": "2027-09-20T09:00:00+09:00", "end": "2027-09-20T10:00:00+09:00"},
        {"start": "2026-09-20T09:00:00Z", "end": "2026-09-20T10:00:00Z"},
        {"end": "2026-09-20T11:00:00+09:00"},
        {"time_zone": "Asia/Seoul"},
        {"summary": "마감까지 5일 남았어요."},
        {"description": BODY},
        {"summary": "비밀번호: PRIVATE-SYNTHETIC-SECRET"},
        {"attendees": "PRIVATE-SYNTHETIC-SECRET"},
        {"event_kind": "ALL_DAY"},
    ],
)
def test_actual_sdk_rejects_unsupported_fields_dates_offsets_quotes_and_private_copy(
    changes, capsys
):
    request, scope = inputs(count=2)
    factory = Factory(proposal(**changes))
    with pytest.raises(
        CalendarDraftOutputError, match="^CALENDAR_DRAFT_OUTPUT_INVALID$"
    ):
        build_calendar_draft(request, scope, factory)
    assert list(factory.models) == ["calendar_draft_planner"]
    assert factory.models["calendar_draft_planner"].stream_calls == 2
    logs = capsys.readouterr().out
    assert "PRIVATE-SYNTHETIC-SECRET" not in logs and BODY not in logs
    assert request.evidence_refs[0] not in logs


def test_verifier_failure_never_returns_the_accepted_draft():
    request, scope = inputs()
    factory = Factory(proposal(), proposal(end="2026-09-20T11:00:00+09:00"))
    with pytest.raises(CalendarDraftOutputError):
        build_calendar_draft(request, scope, factory)
    assert factory.models["calendar_draft_verifier"].stream_calls == 2


def test_all_day_uses_exact_source_date_and_exclusive_next_day():
    request, scope = inputs(
        "개인 제출 건의 마감일은 2026년 9월 20일입니다. 시각은 정해져 있지 않습니다."
    )
    payload = proposal(
        event_kind="ALL_DAY",
        summary="Submission deadline",
        start="2026-09-20",
        end="2026-09-21",
    )
    result = build_calendar_draft(request, scope, Factory(payload, payload))
    assert result["start"] == "2026-09-20" and result["end"] == "2026-09-21"
    assert "timeZone" not in result


@pytest.mark.parametrize(
    "payload",
    [
        proposal(start="2026-09-20T00:00:00+09:00", end="2026-09-20T01:00:00+09:00"),
        proposal(event_kind="ALL_DAY", start="2026-09-20", end="2026-09-22"),
    ],
)
def test_date_only_source_cannot_create_an_invented_clock_or_multi_day_span(payload):
    request, scope = inputs("제출 기한은 2026-09-20입니다.")
    with pytest.raises(CalendarDraftOutputError):
        build_calendar_draft(request, scope, Factory(payload, payload))


def test_appointment_with_no_end_cannot_receive_an_invented_duration():
    request, scope = inputs(
        "예약된 연구 미팅 시작은 2026-09-20T09:00:00+09:00입니다. 종료 시각은 미정입니다."
    )
    with pytest.raises(CalendarDraftOutputError):
        build_calendar_draft(request, scope, Factory())
    assert build_calendar_draft(request, scope, Factory(abstain(), abstain())) is None


def test_point_deadline_is_only_an_explicit_fifteen_minute_marker():
    request, scope = inputs(
        "신청하신 제출 건의 마감은 September 20, 2026 at 8 PM EDT입니다."
    )
    payload = proposal(
        event_kind="POINT_DEADLINE",
        summary="Submission deadline marker",
        start="2026-09-20T20:00:00-04:00",
        end="2026-09-20T20:15:00-04:00",
        description=DEADLINE_BLOCK_DESCRIPTION,
        reason="정확한 마감 시각을 표시하는 15분 블록이에요.",
    )
    result = build_calendar_draft(request, scope, Factory(payload, payload))
    assert result["description"] == DEADLINE_BLOCK_DESCRIPTION
    for invalid in [
        dict(payload, end="2026-09-20T20:30:00-04:00"),
        dict(payload, description=""),
        dict(payload, reason="마감이에요."),
    ]:
        with pytest.raises(CalendarDraftOutputError):
            build_calendar_draft(request, scope, Factory(invalid, invalid))


@pytest.mark.parametrize(
    "cutoff", ["hard cutoff", "submission cutoff", "registration cut-off"]
)
def test_source_cutoff_and_receipt_based_days_support_a_deadline_marker(cutoff):
    request, scope = inputs(
        f"5 days left to submit your project. Sep 17, 8PM EDT is the {cutoff}. "
        "You received this because you signed up for the event."
    )
    payload = proposal(
        event_kind="POINT_DEADLINE",
        summary="Submission deadline marker",
        start="2026-09-17T20:00:00-04:00",
        end="2026-09-17T20:15:00-04:00",
        description=DEADLINE_BLOCK_DESCRIPTION,
        reason="메일 수신일 기준으로 확인한 마감 시각을 표시하는 15분 블록이에요.",
    )
    result = build_calendar_draft(request, scope, Factory(payload, payload))
    assert result["start"] == payload["start"]
    assert result["description"] == DEADLINE_BLOCK_DESCRIPTION


@pytest.mark.parametrize(
    "direct,expected", [(False, "2026-09-15"), (True, "2026-09-17")]
)
def test_relative_days_use_source_receipt_for_mail_and_current_clock_for_direct_request(
    direct, expected
):
    request, scope = inputs(
        "요청한 제출 건의 마감은 3일 후 오후 8시 KST입니다.", direct=direct
    )
    payload = proposal(
        event_kind="POINT_DEADLINE",
        summary="Submission deadline marker",
        start=f"{expected}T20:00:00+09:00",
        end=f"{expected}T20:15:00+09:00",
        description=DEADLINE_BLOCK_DESCRIPTION,
        reason="원문 기준 마감 시각을 15분 블록으로 표시해요.",
    )
    factory = Factory(payload, payload)
    result = build_calendar_draft(request, scope, factory)
    assert result["start"].startswith(expected)
    source = json.loads(
        factory.models["calendar_draft_planner"].received_messages[0][0]["content"][0][
            "text"
        ]
    )["sources"][0]
    assert source["datetime_format_hints"] == [
        {"quote_ref": "m1p1", "rfc3339": f"{expected}T20:00:00+09:00", "time_zone": ""}
    ]


@pytest.mark.parametrize(
    "body,facts",
    [
        ("제출 마감은 3일 후 오후 8시입니다.", []),
        ("제출 마감은 3일 후 오후 8시 KST입니다.", []),
        ("제출 준비에는 3 days 정도 소요됩니다. 오후 8시 KST를 참고하세요.", []),
    ],
)
def test_relative_time_without_a_valid_basis_or_zone_is_not_guessed(body, facts):
    request, scope = inputs(body, facts=facts)
    payload = proposal(
        event_kind="POINT_DEADLINE",
        summary="Submission deadline",
        start="2026-09-17T20:00:00+09:00",
        end="2026-09-17T20:15:00+09:00",
        description=DEADLINE_BLOCK_DESCRIPTION,
        reason="15분 마감 표시 블록이에요.",
    )
    with pytest.raises(CalendarDraftOutputError):
        build_calendar_draft(request, scope, Factory(payload, payload))


@pytest.mark.parametrize(
    "body,facts",
    [
        (None, []),
        ("", []),
        (BODY, ["source_content=snippet_only"]),
        (BODY, ["source_truncated=true"]),
    ],
)
def test_no_full_transient_source_means_no_inference(body, facts):
    request, scope = inputs(body, facts=facts)
    factory = Factory()
    assert build_calendar_draft(request, scope, factory) is None
    assert not factory.models


def test_declared_conflict_abstains_before_inference_and_owner_mismatch_is_denied():
    request, scope = inputs()
    factory = Factory()
    assert (
        build_calendar_draft(
            request.model_copy(update={"conflicting_evidence": True}), scope, factory
        )
        is None
    )
    with pytest.raises(ContextAccessDenied):
        build_calendar_draft(
            request.model_copy(update={"user_id": "other-owner"}), scope, factory
        )
    with pytest.raises(ContextAccessDenied):
        build_calendar_draft(
            request.model_copy(update={"evidence_refs": ["foreign-source"]}),
            scope,
            factory,
        )
    assert not factory.models


def test_past_event_proposal_is_rejected_and_verified_fyi_abstention_returns_none():
    request, scope = inputs(BODY.replace("2026-09-20", "2020-09-20"))
    payload = proposal(
        start="2020-09-20T09:00:00+09:00", end="2020-09-20T10:00:00+09:00"
    )
    with pytest.raises(CalendarDraftOutputError):
        build_calendar_draft(request, scope, Factory(payload, payload))
    request, scope = inputs("연구 업계의 새로운 소식을 소개하는 일반 뉴스레터입니다.")
    assert build_calendar_draft(request, scope, Factory(abstain(), abstain())) is None


@pytest.mark.parametrize("field", list(proposal()))
def test_every_model_facing_scalar_is_required_and_missing_fields_fail_closed(field):
    request, scope = inputs()
    payload = proposal()
    del payload[field]
    factory = Factory(payload, payload)
    with pytest.raises(CalendarDraftOutputError):
        build_calendar_draft(request, scope, factory)
    assert factory.models["calendar_draft_planner"].stream_calls == 2


def test_credentials_are_removed_before_either_model_receives_transient_source():
    secret = "SYNTHETIC-CREDENTIAL-VALUE"
    request, scope = inputs(BODY + f" Password: {secret}")
    factory = Factory()
    result = build_calendar_draft(request, scope, factory)
    assert secret not in json.dumps(result)
    assert all(
        secret not in json.dumps(model.received_messages)
        for model in factory.models.values()
    )


def test_source_passages_retain_all_transient_characters_without_returning_quotes():
    body = "연구 미팅의 배경 설명입니다. " * 45 + BODY
    request, scope = inputs(body)
    # ABSTAIN still receives all allowed source, without persisting a quote.
    factory = Factory(abstain(), abstain())
    assert build_calendar_draft(request, scope, factory) is None
    prompt = json.loads(
        factory.models["calendar_draft_planner"].received_messages[0][0]["content"][0][
            "text"
        ]
    )
    passages = prompt["sources"][0]["passages"]
    assert "".join(passages.values()) == " ".join(("연구 미팅 준비 " + body).split())
    assert all(len(value) <= 700 for value in passages.values())


def test_explicit_iana_zone_is_retained_only_with_matching_source_offset():
    body = BODY + " 시간대는 Asia/Seoul입니다."
    request, scope = inputs(body)
    payload = proposal(time_zone="Asia/Seoul")
    result = build_calendar_draft(request, scope, Factory(payload, payload))
    assert result["timeZone"] == "Asia/Seoul"
    bad = proposal(
        time_zone="Asia/Seoul",
        start="2026-09-20T09:00:00+08:00",
        end="2026-09-20T10:00:00+08:00",
    )
    with pytest.raises(CalendarDraftOutputError):
        build_calendar_draft(request, scope, Factory(bad, bad))


def inspect_source_format(body, *, facts=None):
    request, scope = inputs(body, facts=facts)
    factory = Factory(abstain(), abstain())
    # A mechanically supported instant is not authority or a reason to create an event.
    assert build_calendar_draft(request, scope, factory) is None
    sources = []
    schemas = []
    for model in factory.models.values():
        assert model.stream_calls == 1
        sources.append(
            json.loads(model.received_messages[0][0]["content"][0]["text"])["sources"]
        )
        schemas.append(model.schema)
    assert len(sources) == 2 and sources[0] == sources[1]
    assert schemas[0] == schemas[1]
    zone_property = schemas[0]["properties"]["time_zone"]
    choices = zone_property.get("enum", [zone_property.get("const")])
    return sources[0][0], choices


@pytest.mark.parametrize(
    "text",
    [
        "Sep 20, 2026 at 8 PM EDT.",
        "2026-09-20 오후 8시 KST입니다.",
        "2026-09-20 오후 8시 UTC +05:30입니다.",
        "2026-09-20 오후 8시 Fake/Invalid_Zone입니다.",
        "2026-09-20 오후 8시 Asia/Seoul.invalid입니다.",
        "2026-09-20 오후 8시 UTC.invalid입니다.",
        "참고 https://example.invalid/Asia/Seoul",
    ],
)
def test_source_without_an_explicit_valid_iana_zone_offers_only_empty_time_zone(text):
    source, choices = inspect_source_format(text)
    assert choices == [""]
    assert source["explicit_time_zones"] == []


@pytest.mark.parametrize(
    "zone", ["Asia/Seoul", "America/New_York", "Europe/Paris", "UTC"]
)
def test_only_source_spelled_valid_iana_identifiers_enter_the_schema(zone):
    source, choices = inspect_source_format(
        f"일정 시간대는 {zone}. 미확정 값은 Fake/Invalid_Zone입니다."
    )
    assert choices == ["", zone]
    assert source["explicit_time_zones"] == [zone]


@pytest.mark.parametrize(
    "text,instant,zone",
    [
        (
            "마감은 September 20, 2026 at 8 PM EDT입니다.",
            "2026-09-20T20:00:00-04:00",
            "",
        ),
        (
            "마감은 2026-09-20 오후 8시 UTC+05:30입니다.",
            "2026-09-20T20:00:00+05:30",
            "",
        ),
        (
            "마감은 2026-09-20 오후 8시 Asia/Seoul입니다.",
            "2026-09-20T20:00:00+09:00",
            "Asia/Seoul",
        ),
        (
            "마감은 October 15, 2026 at 8 PM America/New_York.",
            "2026-10-15T20:00:00-04:00",
            "America/New_York",
        ),
        (
            "5 days left to submit. Sep 17, 8PM EDT is the cutoff.",
            "2026-09-17T20:00:00-04:00",
            "",
        ),
        ("마감은 2026-09-20T20:00:00-04:00입니다.", "2026-09-20T20:00:00-04:00", ""),
        ("마감은 2026-09-20T20:00:00Z입니다.", "2026-09-20T20:00:00+00:00", ""),
    ],
)
def test_unique_supported_source_instants_receive_offset_preserving_format_hints(
    text, instant, zone
):
    source, _ = inspect_source_format(text)
    assert source["datetime_format_hints"] == [
        {"quote_ref": "m1p1", "rfc3339": instant, "time_zone": zone}
    ]
    assert source["datetime_format_hints"][0]["quote_ref"] in source["passages"]


@pytest.mark.parametrize(
    "text",
    [
        "2026-09-20 또는 2026-09-21 오후 8시 KST입니다.",
        "2026-09-20 8 PM or 9 PM EDT.",
        "2026-09-20 8 PM 또는 2026-09-21 9 PM EDT.",
        "2026-09-20 8 PM EDT or PDT.",
        "2026-09-20 8 PM Asia/Seoul or America/New_York.",
        "Sep 20 at 8 PM EDT.",
        "2026-09-20 at 8 PM.",
        "2026-09-20 KST.",
        "마감은 3 days left or 5 days left at 8 PM EDT.",
        "2026-09-20 또는 3일 후 오후 8시 KST입니다.",
        "2026-11-01 1:30 AM America/New_York.",
        "2026-03-08 2:30 AM America/New_York.",
        "2026-09-20 8 PM Asia/Seoul EDT.",
        "참고 https://example.invalid/2026-09-20T20:00:00-04:00",
    ],
)
def test_ambiguous_or_incomplete_source_does_not_generate_speculative_instant_combinations(
    text,
):
    source, _ = inspect_source_format(text)
    assert source["datetime_format_hints"] == []


def test_hints_do_not_borrow_a_zone_from_another_passage_or_guess_a_receipt():
    source, _ = inspect_source_format("2026-09-20 8 PM. " + "배경 설명 " * 180 + "EDT.")
    assert len(source["passages"]) > 1
    assert source["datetime_format_hints"] == []
    source, _ = inspect_source_format("마감은 3일 후 오후 8시 KST입니다.", facts=[])
    assert source["datetime_format_hints"] == []


def test_mail_routine_can_prepare_calendar_draft_with_both_fresh_assessments():
    request, scope = inputs()
    request = request.model_copy(update={"case_type": CaseType.ROUTINE_DISCOVERY})
    factory = Factory()
    result = build_calendar_draft(request, scope, factory)
    assert result["start"] == proposal()["start"]
    assert set(factory.models) == {"calendar_draft_planner", "calendar_draft_verifier"}
    assert "approved" not in result and "authorization" not in result


def test_mail_routine_cannot_replace_its_source_with_direct_text():
    request, scope = inputs(direct=True)
    request = request.model_copy(update={"case_type": CaseType.ROUTINE_DISCOVERY})
    factory = Factory()
    assert build_calendar_draft(request, scope, factory) is None
    assert factory.models == {}
