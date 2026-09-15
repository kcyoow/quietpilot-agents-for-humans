from __future__ import annotations

import json
import unicodedata

import pytest
from quietpilot_agent.agentcore_runtime import _google_signal_response
from quietpilot_agent.discovery_copy import CopyLanguageError, validate_display_copy
from quietpilot_agent.local_model import DeterministicModel, ModelPlan
from quietpilot_agent.models import DiscoveryAssessment, DiscoveryBatchAssessment


@pytest.mark.parametrize(
    ("value", "sources", "allow_name"),
    [
        ("Google Calendar에서 일정을 확인해요.", (), False),
        ("The Last of Us 일정을 확인해요.", (), False),
        ("The Last of Us", ("The Last of Us 행사 안내",), True),
        ("Google Calendar", ("Google Calendar 행사 안내",), True),
        (
            "'Please submit the report.' 내용을 확인해요.",
            ("Please submit the report.",),
            False,
        ),
        (
            "東京ディズニーランド 예약을 확인해요.",
            ("東京ディズニーランド 예약",),
            False,
        ),
        ("“東京ディズニーランド” 예약 확인", ("東京ディズニーランド 예약",), True),
        (
            "원문 ‘予約を確認してください’를 확인해요.",
            ("予約を確認してください",),
            False,
        ),
        (
            "‘Please submit the report.’ 내용을 확인해요.",
            ("Please submit the report.",),
            False,
        ),
        ("“Please submit the report.”", ("Please submit the report.",), True),
        ("`npm run build` 결과를 확인해요.", (), False),
        ("https://example.com/カレンダー 내용을 확인해요.", (), False),
        ("case:follow_up-123", (), True),
        ("2026-09-06T09:00:00Z", (), True),
        ("120", (), True),
    ],
)
def test_display_copy_preserves_source_names_quotes_and_technical_values(
    value: str,
    sources: tuple[str, ...],
    allow_name: bool,
) -> None:
    validate_display_copy(value, evidence_texts=sources, allow_source_name=allow_name)


@pytest.mark.parametrize("allow_name", [False, True])
@pytest.mark.parametrize(
    "value",
    [
        "Please submit the report.",
        "Please Review",
        "予約を確認してください",
        "Review the deadline.",
        "Your appointment is tomorrow.",
        "확인해요. Please submit the report.",
    ],
)
def test_generated_sentence_requires_korean_even_if_it_matches_the_source(
    value: str,
    allow_name: bool,
) -> None:
    with pytest.raises(CopyLanguageError, match="^copy_korean_required$"):
        validate_display_copy(
            value,
            evidence_texts=(value,),
            allow_source_name=allow_name,
        )


def test_unexpected_kana_is_rejected_without_rewriting_or_echoing_copy() -> None:
    value = "마감 カクニン 내용을 확인해요."
    with pytest.raises(CopyLanguageError, match="^copy_unexpected_kana$") as caught:
        validate_display_copy(value, evidence_texts=("과제 제출 기한",))
    assert str(caught.value) == "copy_unexpected_kana"
    assert value == "마감 カクニン 내용을 확인해요."


def test_decomposed_hangul_and_source_names_are_valid_without_rewriting() -> None:
    text = unicodedata.normalize("NFD", "東京ディズニーランド 예약을 확인해요.")
    validate_display_copy(text, evidence_texts=("東京ディズニーランド 예약",))
    assert text != unicodedata.normalize("NFC", text)


class _CopyFactory:
    def __init__(self, bad_copy: str, *, invalid_individual_runs: int) -> None:
        self.bad_copy = bad_copy
        self.invalid_individual_runs = invalid_individual_runs
        self.individual_calls = 0
        self.individual_models: list[DeterministicModel] = []

    def create(self, role: str, plan: ModelPlan) -> DeterministicModel:
        output = plan.output
        if role == "discovery_batch_planner":
            assert isinstance(output, DiscoveryBatchAssessment)
            output = output.model_copy(
                update={
                    "opportunities": [
                        item.model_copy(update={"summary": self.bad_copy})
                        for item in output.opportunities
                    ],
                }
            )
        elif role == "discovery_planner":
            self.individual_calls += 1
            assert isinstance(output, DiscoveryAssessment)
            if (
                self.individual_calls <= self.invalid_individual_runs
                and output.opportunity
            ):
                output = output.model_copy(
                    update={
                        "opportunity": output.opportunity.model_copy(
                            update={"summary": self.bad_copy},
                        ),
                    }
                )
        model = DeterministicModel(role, ModelPlan(steps=plan.steps, output=output))
        if role == "discovery_planner":
            self.individual_models.append(model)
        return model


def _source(title: str = "과제 제출 마감") -> dict[str, object]:
    return {
        "status": "SYNCED",
        "evidence": [
            {
                "ref": "mail:deadline",
                "revision": 1,
                "source": "gmail",
                "title": title,
                "facts": [],
                "untrusted_text": "Source-only private body marker.",
            }
        ],
    }


@pytest.mark.parametrize(
    ("bad_copy", "code"),
    [
        ("마감 준비를 확인해요.", "copy_korean_required"),
        ("마감을 カクニン해요.", "copy_unexpected_kana"),
    ],
)
def test_copy_rejection_reaches_existing_individual_repair_and_recovers(
    bad_copy: str,
    code: str,
    capsys,
) -> None:
    factory = _CopyFactory(bad_copy, invalid_individual_runs=2)
    result = _google_signal_response(
        user_id="user-a",
        source=_source(),
        model_factory=factory,
    )
    assert factory.individual_calls == 3
    assert result["unresolved_evidence_count"] == 0
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["evidence_refs"] == ["mail:deadline"]
    assert candidate["proposed_actions"][0]["verb"] == "prepare_task"
    assert (
        candidate["proposed_actions"][0]["parameters"]["source_ref"] == "mail:deadline"
    )
    assert all(
        code in json.dumps(model.received_messages)
        for model in factory.individual_models
    )
    logs = capsys.readouterr().out
    assert f'"rejection_code":"{code}"' in logs
    assert bad_copy not in logs
    assert "Source-only private body marker" not in logs


def test_unrepaired_language_never_becomes_an_empty_success() -> None:
    factory = _CopyFactory("마감 준비를 확인해요.", invalid_individual_runs=99)
    result = _google_signal_response(
        user_id="user-a",
        source=_source(),
        model_factory=factory,
    )
    assert factory.individual_calls == 3
    assert result["candidate"] is None
    assert result["candidates"] == []
    assert result["unresolved_evidence_count"] == 1


def test_valid_suppression_does_not_enter_language_repair() -> None:
    factory = _CopyFactory("마감 준비를 확인해요.", invalid_individual_runs=99)
    result = _google_signal_response(
        user_id="user-a",
        source=_source("Weekly newsletter"),
        model_factory=factory,
    )
    assert factory.individual_calls == 0
    assert result["candidates"] == []
    assert result["unresolved_evidence_count"] == 0


def test_other_batch_evidence_does_not_excuse_kana_in_an_opportunity() -> None:
    source = _source()
    source["evidence"].append(
        {
            "ref": "mail:other",
            "revision": 1,
            "source": "gmail",
            "title": "カクニン newsletter",
            "facts": [],
            "untrusted_text": "",
        }
    )
    factory = _CopyFactory("마감을 カクニン해요.", invalid_individual_runs=99)
    result = _google_signal_response(
        user_id="user-a", source=source, model_factory=factory
    )
    assert result["candidates"] == []
    assert result["unresolved_evidence_count"] == 1
    # Three rejected attempts for the deadline, then one valid SUPPRESS for the other mail.
    assert factory.individual_calls == 4


class _MachineFieldsFactory:
    def create(self, role: str, plan: ModelPlan) -> DeterministicModel:
        assert isinstance(plan.output, DiscoveryBatchAssessment)
        opportunity = plan.output.opportunities[0]
        action = opportunity.proposed_actions[0]
        action = action.model_copy(
            update={
                "target_resource": "case:カレンダー-123",
                "parameters": {
                    **action.parameters,
                    "title": "Prepare submission items",
                    "due": "Friday, September 6, 2026",
                    "start": "2026-09-06T09:00:00Z",
                    "remindBeforeMinutes": 120,
                    "internal_note": "Please keep this machine field unchanged.",
                },
            }
        )
        output = plan.output.model_copy(
            update={
                "opportunities": [
                    opportunity.model_copy(update={"proposed_actions": [action]})
                ],
            }
        )
        return DeterministicModel(role, ModelPlan(steps=plan.steps, output=output))


def test_language_check_leaves_non_display_machine_fields_untouched() -> None:
    result = _google_signal_response(
        user_id="user-a",
        source=_source(),
        model_factory=_MachineFieldsFactory(),
    )
    action = result["candidates"][0]["proposed_actions"][0]
    assert action["target_resource"] == "case:カレンダー-123"
    assert action["parameters"]["due"] == "Friday, September 6, 2026"
    assert (
        action["parameters"]["internal_note"]
        == "Please keep this machine field unchanged."
    )
    assert action["parameters"]["source_ref"] == "mail:deadline"
