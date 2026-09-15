from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError
from quietpilot_agent.agentcore_runtime import (
    GoogleConnectorInvocation,
    _google_interest_response,
    run_agentcore_invocation,
)
from quietpilot_agent.google_connector import GMAIL_READONLY_SCOPE, GoogleConnector
from quietpilot_agent.local_model import DeterministicModel, ModelPlan, ToolStep
from quietpilot_agent.mail_interests import (
    InterestMailAssessment,
    MailInterestAssessment,
    MailInterestOutputError,
    MailInterestProfile,
    MailTagAssessment,
    MailTitle,
    RecommendedMailTag,
    match_interest_mail,
    recommend_mail_tags,
)
from quietpilot_agent.models import EvidenceRecord, StrictModel
from strands.tools.structured_output import convert_pydantic_to_tool_spec
from strands.types.exceptions import StructuredOutputException

FixtureMailModel = runpy.run_path(
    str(Path(__file__).with_name("mail_scripted_model.py"))
)["FixtureMailModel"]


def _ref(message_id: str) -> str:
    return f"gmail:{hashlib.sha256(message_id.encode()).hexdigest()}"


@pytest.mark.parametrize(
    "model,field,values",
    [
        (MailTagAssessment, "tags", {"assessed_title_refs": [], "tags": []}),
        (
            MailInterestAssessment,
            "matches",
            {"assessed_evidence_refs": [], "matches": []},
        ),
        (
            InterestMailAssessment,
            "matched_tags",
            {
                "evidence_ref": "inside",
                "summary": "Information from your school.",
                "reason": "Matches a saved interest.",
                "matched_tags": [],
            },
        ),
    ],
)
def test_strands_output_schema_requires_arrays_and_matches_null_rejection(
    model, field, values
):
    schema = convert_pydantic_to_tool_spec(model)["inputSchema"]["json"]
    assert field in schema["required"]
    assert schema["properties"][field]["type"] == "array"
    assert model.model_validate(values).model_dump()[field] == []
    with pytest.raises(ValidationError) as failure:
        model.model_validate({**values, field: None})
    assert any(
        error["loc"] == (field,) and error["type"] == "list_type"
        for error in failure.value.errors()
    )


def test_nested_strands_match_schema_keeps_required_arrays_and_profile_defaults():
    schema = convert_pydantic_to_tool_spec(MailInterestAssessment)["inputSchema"][
        "json"
    ]
    match = schema["properties"]["matches"]["items"]
    assert "matched_tags" in match["required"]
    assert match["properties"]["matched_tags"]["type"] == "array"
    profile = MailInterestProfile(revision=1)
    assert profile.tags == [] and profile.description == ""


@pytest.mark.parametrize(
    "error_class", [TypeError, ValueError, StructuredOutputException, ValidationError]
)
def test_typed_output_failure_logs_categories_without_private_content(
    error_class, monkeypatch, capsys
):
    private = "PRIVATE-MAIL-CONTENT-AND-TOKEN"
    if error_class is ValidationError:
        try:
            MailTagAssessment.model_validate(
                {"assessed_title_refs": [], "tags": None, private: private}
            )
        except ValidationError as failure:
            error = failure
    else:
        error = error_class(private)

    class FailingAgent:
        def __init__(self, **kwargs):
            guard = kwargs["hooks"][0]
            guard.attempts = 2
            guard.failures = 1
            guard.protocol_violation = True
            guard.missing_output_failures = 1

        def __call__(self, *args, **kwargs):
            raise error

    monkeypatch.setattr("quietpilot_agent.mail_interests.Agent", FailingAgent)
    with pytest.raises(MailInterestOutputError, match="^MAIL_INTEREST_OUTPUT_INVALID$"):
        recommend_mail_tags([MailTitle(ref=private, title=private)], _Factory())

    captured = capsys.readouterr()
    records = [json.loads(line) for line in captured.out.splitlines()]
    assert len(records) == 1
    diagnostic = records[0]
    assert diagnostic["role"] == "mail_interest_tags"
    assert diagnostic["error_class"] == error_class.__name__
    assert diagnostic["attempts"] == 2 and diagnostic["failures"] == 1
    assert diagnostic["protocol_violation"] is True
    assert diagnostic["missing_output_failures"] == 1
    assert private not in captured.out + captured.err
    if error_class is ValidationError:
        assert diagnostic["validation_errors"] == [
            {"loc": ["tags"], "type": "list_type"},
            {"loc": ["unknown_field"], "type": "extra_forbidden"},
        ]
    else:
        assert "validation_errors" not in diagnostic


@pytest.mark.parametrize(
    "stop_reason",
    [
        None,
        "end_turn",
        "limit_turns",
        "malformed_tool_use",
        "malformed_model_output",
        "model_context_window_exceeded",
    ],
)
def test_missing_output_stays_failed_and_preserves_provider_reason(
    monkeypatch, capsys, stop_reason
):
    calls = []

    class UntypedAgent:
        def __init__(self, **kwargs):
            pass

        def __call__(self, *args, **kwargs):
            calls.append(1)
            return SimpleNamespace(structured_output=None, stop_reason=stop_reason)

        def structured_output(self, *args):
            pytest.fail("Missing output must not trigger unbounded extra inference")

    monkeypatch.setattr("quietpilot_agent.mail_interests.Agent", UntypedAgent)
    with pytest.raises(MailInterestOutputError):
        recommend_mail_tags([MailTitle(ref="owned", title="학교 소식")], _Factory())
    assert calls == [1]
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["stop_reason"] == (stop_reason or "unknown")
    assert diagnostic["output_shape"] == "none"


@pytest.mark.parametrize(
    "rejection",
    ["protocol", "cancelled", "interrupt", "guardrail_intervened", "content_filtered"],
)
def test_protocol_failure_cannot_trigger_an_extra_explicit_inference(
    monkeypatch, rejection
):
    class RejectedAgent:
        def __init__(self, **kwargs):
            kwargs["hooks"][0].protocol_violation = rejection == "protocol"

        def __call__(self, *args, **kwargs):
            return SimpleNamespace(
                structured_output=None,
                stop_reason=None if rejection == "protocol" else rejection,
            )

        def structured_output(self, *args):
            pytest.fail("A protocol rejection must stay terminal")

    monkeypatch.setattr("quietpilot_agent.mail_interests.Agent", RejectedAgent)
    with pytest.raises(MailInterestOutputError):
        recommend_mail_tags([MailTitle(ref="owned", title="학교 소식")], _Factory())


@pytest.mark.parametrize("foreign_ref", [False, True])
def test_provider_model_is_canonically_revalidated_before_grounding(
    monkeypatch, foreign_ref
):
    class ProviderOutput(StrictModel):
        assessed_title_refs: list[str]
        tags: list[RecommendedMailTag]

    class ProviderAgent:
        def __init__(self, **kwargs):
            pass

        def __call__(self, *args, **kwargs):
            ref = "foreign" if foreign_ref else "m1"
            return SimpleNamespace(
                structured_output=ProviderOutput(
                    assessed_title_refs=[ref],
                    tags=[RecommendedMailTag(tag="학교", evidence_refs=[ref])],
                )
            )

    monkeypatch.setattr("quietpilot_agent.mail_interests.Agent", ProviderAgent)
    if foreign_ref:
        with pytest.raises(MailInterestOutputError):
            recommend_mail_tags([MailTitle(ref="owned", title="학교 안내")], _Factory())
    else:
        tags = recommend_mail_tags(
            [MailTitle(ref="owned", title="학교 안내")], _Factory()
        )
        assert tags == [RecommendedMailTag(tag="학교", evidence_refs=["owned"])]


class _Factory:
    def __init__(self, **outputs: StrictModel | dict[str, object]) -> None:
        self.outputs = outputs
        self.models: dict[str, DeterministicModel] = {}

    def create(self, role: str, plan: ModelPlan) -> DeterministicModel:
        output = self.outputs.get(
            role,
            self.outputs.get("mail_interest_matcher", plan.output)
            if role == "mail_interest_verifier"
            else plan.output,
        )

        if isinstance(output, dict):
            step = ToolStep("MailFieldAssessment", output)
            prepared = ModelPlan(steps=(*plan.steps, *([step] * 4)), output=plan.output)
        else:
            prepared = ModelPlan(steps=plan.steps, output=output)
        model = FixtureMailModel(role, prepared)
        self.models[role] = model
        return model


class _Identity:
    def get_resource_oauth2_token(self, **values: object) -> dict[str, str]:
        assert values["scopes"] == [GMAIL_READONLY_SCOPE]
        return {"accessToken": "offline-provider-token"}


class _Google:
    def __init__(self, titles: list[str], *, page_size: int = 32) -> None:
        self.titles = titles
        self.page_size = page_size
        self.calls: list[tuple[str, str]] = []

    def request_json(
        self, method: str, url: str, **values: object
    ) -> dict[str, object]:
        self.calls.append((method, url))
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/profile"):
            return {"emailAddress": "owner@example.test", "historyId": "45"}
        if parsed.path.endswith("/watch"):
            assert method == "POST"
            return {"historyId": "46", "expiration": "1790000000000"}
        assert method == "GET"
        if parsed.path.endswith("/messages"):
            assert query["q"] == ["newer_than:7d"]
            assert query["labelIds"] == ["INBOX"]
            offset = int(query.get("pageToken", ["0"])[0])
            stop = min(
                offset + self.page_size,
                offset + int(query["maxResults"][0]),
                len(self.titles),
            )
            result: dict[str, object] = {
                "messages": [{"id": f"mail-{index}"} for index in range(offset, stop)],
                "resultSizeEstimate": len(self.titles),
            }
            if stop < len(self.titles):
                result["nextPageToken"] = str(stop)
            return result
        if "/messages/" in parsed.path:
            message_id = parsed.path.rsplit("/", 1)[1]
            index = int(message_id.removeprefix("mail-"))
            return {
                "id": message_id,
                "internalDate": "1788000000000",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": self.titles[index]},
                        {"name": "From", "value": "SENDER-PRIVATE@example.test"},
                    ],
                    "body": {"data": "BODY-PRIVATE"},
                },
                "snippet": "SNIPPET-PRIVATE",
            }
        raise AssertionError("Unexpected Google operation")


def _connector(google: _Google) -> GoogleConnector:
    return GoogleConnector(
        identity=_Identity(),
        google=google,
        gmail_topic_name="projects/example/topics/mail",
        oauth_return_url="https://example.test/oauth-return",
    )


def _run(operation: str, google: _Google, factory: _Factory) -> dict[str, object]:
    return run_agentcore_invocation(
        {"operation": operation, "user_id": "owner"},
        SimpleNamespace(session_id="offline", request_headers={}, request=None),
        google_connector=_connector(google),
        workload_access_token="offline-workload-token",
        model_factory=factory,
    )


def test_title_recommendations_never_forward_extra_sender_snippet_or_body() -> None:
    google = _Google(["학교 도서관 이용 안내"])
    factory = _Factory(
        mail_interest_tags=MailTagAssessment(
            assessed_title_refs=["m1"],
            tags=[RecommendedMailTag(tag="학교", evidence_refs=["m1"])],
        )
    )

    result = _run("GOOGLE_INTEREST_TAGS", google, factory)

    model = factory.models["mail_interest_tags"]
    messages = json.dumps(model.received_messages, ensure_ascii=False)
    assert "학교 도서관 이용 안내" in messages
    assert all(
        value not in messages
        for value in ["SENDER-PRIVATE", "SNIPPET-PRIVATE", "BODY-PRIVATE"]
    )
    assert _ref("mail-0") not in messages
    assert "internalDate" not in messages
    assert "1788000000000" not in messages
    assert model.seen_tool_names == [frozenset({"MailTagAssessment"})]
    assert model.stream_calls == 1
    assert result["tags"] == [{"tag": "학교", "evidence_refs": [_ref("mail-0")]}]
    assert result["title_count"] == 1
    assert result["sampled"] is False
    for _, url in google.calls:
        parsed = urlparse(url)
        if "/messages/" in parsed.path:
            assert parse_qs(parsed.query) == {
                "format": ["metadata"],
                "metadataHeaders": ["Subject"],
                "fields": ["id,payload/headers(name,value)"],
            }
    assert "PRIVATE" not in json.dumps(result)
    assert "token" not in json.dumps(result)


def test_title_sample_is_bounded_across_pages_and_discloses_truncation() -> None:
    google = _Google([f"학교 소식 {index}" for index in range(45)], page_size=20)
    result = _run("GOOGLE_INTEREST_TAGS", google, _Factory())

    assert result["title_count"] == 32
    assert result["sampled"] is True
    assert sum("/messages/" in urlparse(url).path for _, url in google.calls) == 32
    assert (
        len(
            [url for _, url in google.calls if urlparse(url).path.endswith("/messages")]
        )
        == 2
    )


def test_title_sample_completed_across_pages_is_not_marked_truncated() -> None:
    google = _Google([f"소식 {index}" for index in range(3)], page_size=2)
    result = _run("GOOGLE_INTEREST_TAGS", google, _Factory())
    assert result["title_count"] == 3
    assert result["sampled"] is False


@pytest.mark.parametrize("titles", [[], ["", "  "]])
def test_empty_titles_do_not_invoke_a_model_or_create_suggestions(
    titles: list[str],
) -> None:
    factory = _Factory()
    result = _run("GOOGLE_INTEREST_TAGS", _Google(titles), factory)
    assert result["tags"] == []
    assert result["title_count"] == 0
    assert factory.models == {}


def test_mail_setup_registers_watch_without_reading_mail_or_invoking_models() -> None:
    google = _Google(["Must not be read"])
    factory = _Factory()
    result = _run("GOOGLE_MAIL_SETUP", google, factory)

    assert result["status"] == "CONNECTED"
    assert result["history_id"] == "46"
    assert result["scopes"] == [GMAIL_READONLY_SCOPE]
    assert result["account_hash"] == hashlib.sha256(b"owner@example.test").hexdigest()
    assert [urlparse(url).path.rsplit("/", 1)[1] for _, url in google.calls] == [
        "profile",
        "watch",
    ]
    assert "recent_message_estimate" not in result
    assert "evidence" not in result
    assert factory.models == {}


def _evidence(ref: str, title: str, text: str) -> EvidenceRecord:
    return EvidenceRecord(
        user_id="owner",
        ref=ref,
        revision=1,
        source="gmail",
        title=title,
        facts=["sender_domain=university.test", "received_at_unix_ms=1788000000000"],
        untrusted_text=text,
    )


def _match(ref: str, tags: list[str] | None = None) -> InterestMailAssessment:
    return InterestMailAssessment(
        evidence_ref=ref,
        summary="Explains how to use the school library.",
        reason="Matches the selected school news topic.",
        matched_tags=["학교"] if tags is None else tags,
    )


def test_informational_mail_is_returned_and_unrelated_mail_never_reaches_discovery() -> (
    None
):
    school = _evidence("gmail:school", "학교 도서관 이용 안내", "LIBRARY-SOURCE")
    unrelated = _evidence("gmail:shopping", "쇼핑 할인 신청 마감", "SHOPPING-PRIVATE")
    profile = MailInterestProfile(
        revision=4,
        tags=["학교"],
        description="장학금과 도서관 소식을 보고 싶어요. 광고와 쇼핑 메일은 제외해 줘.",
    )
    factory = _Factory(
        mail_interest_matcher=MailInterestAssessment(
            assessed_evidence_refs=["m1", "m2"],
            matches=[_match("m1")],
        )
    )

    result = _google_interest_response(
        user_id="owner",
        profile=profile,
        source={
            "status": "SCAN_PAGE",
            "processed_message_count": 2,
            "next_page_token": None,
            "completion_history_id": "47",
            "evidence": [record.model_dump() for record in [school, unrelated]],
        },
        model_factory=factory,
    )

    assert result["interest_validated"] is True
    assert result["interest_profile_revision"] == 4
    assert len(result["interest_matches"]) == 1
    item = result["interest_matches"][0]
    assert item["evidence_ref"] == school.ref
    assert item["title"] == school.title
    assert item["sender_domain"] == "university.test"
    assert item["received_at"].endswith("Z")
    assert result["candidates"] == []
    assert len(result["evidence"]) == result["processed_message_count"]
    assert all(record["untrusted_text"] is None for record in result["evidence"])
    assert "SHOPPING-PRIVATE" not in json.dumps(result)
    matcher_messages = json.dumps(
        factory.models["mail_interest_matcher"].received_messages, ensure_ascii=False
    )
    assert profile.description in matcher_messages
    assert "SHOPPING-PRIVATE" in matcher_messages
    discovery_messages = json.dumps(
        factory.models["discovery_batch_planner"].received_messages, ensure_ascii=False
    )
    assert unrelated.ref not in discovery_messages
    assert "SHOPPING-PRIVATE" not in discovery_messages
    assert "LIBRARY-SOURCE" in discovery_messages


def test_only_matched_actionable_mail_can_also_produce_a_separate_candidate() -> None:
    relevant = _evidence(
        "gmail:school", "학교 과제 제출 마감", "학교 과제를 제출하는 기한이에요."
    )
    unrelated = _evidence("gmail:shopping", "쇼핑 신청 마감", "할인을 신청해요.")
    factory = _Factory(
        mail_interest_matcher=MailInterestAssessment(
            assessed_evidence_refs=["m1", "m2"],
            matches=[_match("m1")],
        )
    )
    result = _google_interest_response(
        user_id="owner",
        profile=MailInterestProfile(revision=1, tags=["학교"]),
        source={"evidence": [record.model_dump() for record in [relevant, unrelated]]},
        model_factory=factory,
    )
    assert len(result["interest_matches"]) == 1
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["evidence_refs"] == [relevant.ref]
    assert "proposed_actions" not in result["interest_matches"][0]


def test_planner_failure_preserves_validated_interest_mail(monkeypatch) -> None:
    relevant = _evidence("gmail:school", "학교 소식", "PRIVATE-MAIL-TEXT")
    factory = _Factory(
        mail_interest_matcher=MailInterestAssessment(
            assessed_evidence_refs=["m1"], matches=[_match("m1")]
        )
    )

    def fail_planner(**kwargs):
        raise RuntimeError("PRIVATE-ERROR-DETAILS")

    monkeypatch.setattr(
        "quietpilot_agent.agentcore_runtime._google_signal_response", fail_planner
    )
    result = _google_interest_response(
        user_id="owner",
        profile=MailInterestProfile(revision=1, tags=["학교"]),
        source={"evidence": [relevant.model_dump()]},
        model_factory=factory,
    )
    assert result["interest_validated"] is True
    assert result["interest_matches"][0]["evidence_ref"] == relevant.ref
    assert result["discovery_validated"] is False
    assert result["unresolved_evidence_count"] == 1
    assert result["candidates"] == [] and result["candidate"] is None
    assert "PRIVATE" not in json.dumps(result)


def test_invalid_mail_match_never_becomes_partial_success(monkeypatch) -> None:
    relevant = _evidence("gmail:school", "학교 소식", "학교 안내")
    factory = _Factory(
        mail_interest_matcher=MailInterestAssessment(
            assessed_evidence_refs=["m1"], matches=[_match("gmail:foreign")]
        )
    )
    monkeypatch.setattr(
        "quietpilot_agent.agentcore_runtime._google_signal_response",
        lambda **kwargs: pytest.fail("Invalid matches must not reach the planner"),
    )
    with pytest.raises(MailInterestOutputError):
        _google_interest_response(
            user_id="owner",
            profile=MailInterestProfile(revision=1, tags=["학교"]),
            source={"evidence": [relevant.model_dump()]},
            model_factory=factory,
        )


@pytest.mark.parametrize(
    "corruption", ["missing_row", "missing_summary", "extra_row", "foreign_tag"]
)
def test_invalid_interest_coverage_and_references_fail_without_empty_success(
    corruption,
):
    payload = {
        "m1_tag_refs": "t1",
        "m1_description_match": "NO",
        "m1_importance": "NORMAL",
        "m1_summary": "Information from your school.",
        "m1_reason": "Provides school news.",
        "m1_source_ref": "m1p1",
    }
    if corruption == "missing_row":
        payload = {}
    elif corruption == "missing_summary":
        del payload["m1_summary"]
    elif corruption == "extra_row":
        payload["m2_summary"] = "Another email."
    else:
        payload["m1_tag_refs"] = "t9"
    factory = _Factory(mail_interest_matcher=payload)
    with pytest.raises(MailInterestOutputError, match="^MAIL_INTEREST_OUTPUT_INVALID$"):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["학교"]),
            [_evidence("gmail:school", "학교 소식", "학내 소식이에요.")],
            factory,
        )


def test_natural_language_only_profile_can_match_without_synthetic_tags() -> None:
    factory = _Factory(
        mail_interest_matcher=MailInterestAssessment(
            assessed_evidence_refs=["m1"],
            matches=[_match("m1", [])],
        )
    )
    result = match_interest_mail(
        MailInterestProfile(
            revision=1, description="학교 소식을 보여 주고 쇼핑은 제외해 줘."
        ),
        [_evidence("gmail:school", "학교 소식", "학내 소식이에요.")],
        factory,
    )
    assert result[0].matched_tags == []


@pytest.mark.parametrize(
    ("assessed", "cited"),
    [
        (["outside"], ["m1"]),
        (["m1"], ["outside"]),
        (["m1"], ["m1", "m1"]),
    ],
)
def test_title_recommendations_require_exact_owned_reference_coverage(
    assessed: list[str], cited: list[str]
) -> None:
    factory = _Factory(
        mail_interest_tags=MailTagAssessment(
            assessed_title_refs=assessed,
            tags=[RecommendedMailTag(tag="학교", evidence_refs=cited)],
        )
    )
    with pytest.raises(MailInterestOutputError):
        recommend_mail_tags([MailTitle(ref="inside", title="학교 안내")], factory)


def test_duplicate_model_tags_fail_schema_validation_without_canned_fallback() -> None:
    tag = RecommendedMailTag(tag="학교", evidence_refs=["m1"])
    invalid = MailTagAssessment(assessed_title_refs=["m1"], tags=[]).model_copy(
        update={"tags": [tag, tag]}
    )
    factory = _Factory(mail_interest_tags=invalid)
    with pytest.raises(MailInterestOutputError):
        recommend_mail_tags([MailTitle(ref="inside", title="학교 안내")], factory)
    assert factory.models["mail_interest_tags"].stream_calls == 2


@pytest.mark.parametrize(
    "tags",
    [
        ["학교", "학교"],
        ["School", "school"],
        ["#학교"],
        ["C#"],
        ["학교 소식"],
        ["학교\t소식"],
        ["＃학교"],
        [" "],
        ["x" * 33],
    ],
)
def test_invalid_profile_tags_are_rejected(tags: list[str]) -> None:
    with pytest.raises(ValidationError):
        MailInterestProfile(revision=1, tags=tags)


def test_topic_names_are_normalized_and_preserve_non_hash_punctuation() -> None:
    assert MailInterestProfile(
        revision=1, tags=[" Ｃ＋＋ ", "node.js", "AI/ML", "학교"]
    ).tags == ["C++", "node.js", "AI/ML", "학교"]


def test_topic_length_is_measured_after_normalization_in_unicode_codepoints() -> None:
    assert MailInterestProfile(revision=1, tags=["😀" * 32]).tags == ["😀" * 32]
    with pytest.raises(ValidationError):
        MailInterestProfile(revision=1, tags=["😀" * 33])
    with pytest.raises(ValidationError):
        MailInterestProfile(revision=1, tags=["㎏" * 17])


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "GOOGLE_INTEREST_SCAN"},
        {
            "operation": "GOOGLE_INTEREST_SCAN",
            "interest_profile": {"revision": 1, "tags": [], "description": " "},
        },
        {
            "operation": "GOOGLE_INTEREST_TAGS",
            "interest_profile": {"revision": 1, "tags": ["학교"]},
        },
        {
            "operation": "GOOGLE_INTEREST_SCAN_PAGE",
            "interest_profile": {"revision": 1, "tags": ["학교"]},
        },
        {
            "operation": "GOOGLE_INTEREST_HISTORY_SYNC",
            "interest_profile": {"revision": 1, "tags": ["학교"]},
        },
    ],
)
def test_interest_invocation_fails_closed_before_connector_access(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        GoogleConnectorInvocation.model_validate({"user_id": "owner", **payload})


def test_empty_interest_page_does_not_run_either_model() -> None:
    factory = _Factory()
    result = _google_interest_response(
        user_id="owner",
        profile=MailInterestProfile(revision=1, tags=["학교"]),
        source={"evidence": []},
        model_factory=factory,
    )
    assert result["interest_matches"] == []
    assert result["candidates"] == []
    assert factory.models == {}


@pytest.mark.parametrize(
    ("operation", "extra", "expected_status"),
    [
        ("GOOGLE_INTEREST_SCAN", {}, "CONNECTED"),
        ("GOOGLE_INTEREST_SCAN_PAGE", {"page_token": "0"}, "SCAN_PAGE"),
        ("GOOGLE_INTEREST_HISTORY_SYNC", {"start_history_id": "40"}, "SYNCED"),
    ],
)
def test_new_interest_operations_use_the_matching_boundary(
    operation: str, extra: dict[str, str], expected_status: str
) -> None:
    class HistoryGoogle(_Google):
        def request_json(
            self, method: str, url: str, **values: object
        ) -> dict[str, object]:
            if urlparse(url).path.endswith("/history"):
                self.calls.append((method, url))
                return {
                    "historyId": "47",
                    "history": [
                        {"id": "47", "messagesAdded": [{"message": {"id": "mail-0"}}]}
                    ],
                }
            return super().request_json(method, url, **values)

    google = HistoryGoogle(["학교 소식"])
    factory = _Factory()
    result = run_agentcore_invocation(
        {
            "operation": operation,
            "user_id": "owner",
            **extra,
            "interest_profile": {
                "revision": 3,
                "tags": ["학교"],
                "description": "광고는 빼줘.",
            },
        },
        SimpleNamespace(session_id="offline", request_headers={}, request=None),
        google_connector=_connector(google),
        workload_access_token="offline-workload-token",
        model_factory=factory,
    )
    assert result["status"] == expected_status
    assert result["interest_validated"] is True
    assert result["interest_profile_revision"] == 3
    assert result["interest_matches"] == []
    assert result["candidates"] == []
    assert set(factory.models) == {"mail_interest_matcher", "mail_interest_verifier"}
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["untrusted_text"] is None
    assert sum(url.endswith("/watch") for _, url in google.calls) == (
        operation == "GOOGLE_INTEREST_SCAN"
    )


def test_full_title_batch_uses_short_aliases_and_restores_all_original_refs():
    titles = [
        MailTitle(ref=_ref(f"title-{index}"), title="학교 도서관 소식")
        for index in range(32)
    ]
    aliases = [f"m{index}" for index in range(1, 33)]
    factory = _Factory(
        mail_interest_tags=MailTagAssessment(
            assessed_title_refs=list(reversed(aliases)),
            tags=[
                RecommendedMailTag(tag="학교", evidence_refs=list(reversed(aliases)))
            ],
        )
    )
    tags = recommend_mail_tags(titles, factory)
    assert tags[0].evidence_refs == [title.ref for title in reversed(titles)]
    messages = json.dumps(
        factory.models["mail_interest_tags"].received_messages, ensure_ascii=False
    )
    assert all(title.ref not in messages for title in titles)
    assert factory.models["mail_interest_tags"].stream_calls == 1


def test_matching_aliases_restore_original_refs_from_reordered_output_fields():
    evidence = [
        _evidence(_ref(f"mail-{index}"), "학교 도서관 안내", "학교 소식이에요.")
        for index in range(8)
    ]
    aliases = [f"m{index}" for index in range(1, 9)]
    factory = _Factory(
        mail_interest_matcher=MailInterestAssessment(
            assessed_evidence_refs=aliases,
            matches=[_match(alias) for alias in reversed(aliases)],
        )
    )
    matches = match_interest_mail(
        MailInterestProfile(revision=1, tags=["학교"]), evidence, factory
    )
    assert [match.evidence_ref for match in matches] == [
        record.ref for record in evidence
    ]
    messages = json.dumps(
        factory.models["mail_interest_matcher"].received_messages, ensure_ascii=False
    )
    assert all(record.ref not in messages for record in evidence)


def test_real_sdk_repairs_foreign_title_citations_before_returning_tags():
    from quietpilot_agent.local_model import ToolStep

    model = DeterministicModel(
        "mail_interest_tags",
        ModelPlan(
            steps=(
                ToolStep(
                    "MailTagAssessment",
                    {
                        "assessed_title_refs": ["m1"],
                        "tags": [{"tag": "학교", "evidence_refs": ["foreign"]}],
                    },
                ),
            ),
            output=MailTagAssessment(
                assessed_title_refs=["m1"],
                tags=[RecommendedMailTag(tag="학교", evidence_refs=["m1"])],
            ),
        ),
    )
    tags = recommend_mail_tags(
        [MailTitle(ref="gmail:school", title="학교 도서관 소식")],
        SimpleNamespace(create=lambda role, plan: model),
    )
    assert tags == [RecommendedMailTag(tag="학교", evidence_refs=["gmail:school"])]
    assert model.stream_calls == 2


def test_invalid_tag_diagnostics_report_only_known_schema_fields(capsys):
    private = "PRIVATE-MAIL-AND-TOKEN"
    factory = _Factory(
        mail_interest_matcher=MailInterestAssessment(
            assessed_evidence_refs=["m1"],
            matches=[_match("m1", [private])],
        )
    )
    with pytest.raises(MailInterestOutputError):
        match_interest_mail(
            MailInterestProfile(revision=1, tags=["학교"]),
            [_evidence("gmail:school", "학교 안내", private)],
            factory,
        )
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.out)
    assert diagnostic["rejected_rules"] == ["unselected_tag"]
    assert (
        diagnostic["tool_validation_errors"]
        == [
            {"loc": ["m1_tag_refs"], "type": "literal_or_type"},
        ]
        * 4
    )
    assert diagnostic["failures"] == diagnostic["tool_errors"] == 4
    assert private not in captured.out + captured.err
