from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from quietpilot_agent import bedrock_model
from quietpilot_agent.bedrock_model import (
    DEFAULT_BEDROCK_MODEL_ID,
    DEFAULT_BEDROCK_REGION,
    MODEL_ID_ENV,
    MODEL_REGION_ENV,
    BedrockModelFactory,
)
from quietpilot_agent.local_model import ModelPlan


def test_factory_defaults_to_nova_2_lite_in_seoul_without_network(
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeBedrockModel:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(bedrock_model, "BedrockModel", FakeBedrockModel)
    factory = BedrockModelFactory.from_environment({})

    model = factory.create("orchestrator", cast(ModelPlan, object()))

    assert isinstance(model, FakeBedrockModel)
    assert calls == [
        {
            "model_id": DEFAULT_BEDROCK_MODEL_ID,
            "region_name": DEFAULT_BEDROCK_REGION,
            "temperature": 0.0,
        }
    ]


def test_factory_accepts_explicit_environment_overrides(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(bedrock_model, "BedrockModel", FakeBedrockModel)
    factory = BedrockModelFactory.from_environment(
        {
            MODEL_ID_ENV: "global.openai.gpt-5.6-sol",
            MODEL_REGION_ENV: "ap-northeast-1",
        }
    )

    factory.create("signal_analyst", cast(ModelPlan, object()))

    assert captured == {
        "model_id": "global.openai.gpt-5.6-sol",
        "region_name": "ap-northeast-1",
        "temperature": 0.0,
    }


@pytest.mark.parametrize(("field", "value"), [("model_id", ""), ("region", "  ")])
def test_factory_rejects_empty_configuration(field: str, value: str) -> None:
    values = {
        "model_id": DEFAULT_BEDROCK_MODEL_ID,
        "region": DEFAULT_BEDROCK_REGION,
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        BedrockModelFactory(**values)


@pytest.mark.parametrize(
    "role", ["mail_interest_tags", "mail_interest_matcher", "mail_interest_verifier"]
)
def test_mail_assessments_receive_an_explicit_bounded_output_budget(monkeypatch, role):
    calls = []
    monkeypatch.setattr(
        bedrock_model, "BedrockModel", lambda **kwargs: calls.append(kwargs)
    )
    BedrockModelFactory().create(role, cast(ModelPlan, object()))
    options = {"max_tokens": 4096}
    if role == "mail_interest_verifier":
        options = {
            "max_tokens": 10000,
            "additional_request_fields": {
                "reasoningConfig": {"type": "enabled", "maxReasoningEffort": "low"}
            },
        }
    assert calls == [
        {
            "model_id": DEFAULT_BEDROCK_MODEL_ID,
            "region_name": DEFAULT_BEDROCK_REGION,
            "temperature": 0.0,
            **options,
        }
    ]


@pytest.mark.parametrize("model_id", [DEFAULT_BEDROCK_MODEL_ID, "another-model"])
def test_real_sdk_serializes_nova_verifier_reasoning_without_network(
    monkeypatch, model_id
):
    class Session:
        def client(self, **kwargs):
            assert kwargs["service_name"] == "bedrock-runtime"
            return SimpleNamespace(
                meta=SimpleNamespace(region_name=kwargs["region_name"])
            )

    monkeypatch.setattr("strands.models.bedrock.boto3.Session", Session)
    model = BedrockModelFactory(model_id=model_id).create(
        "mail_interest_verifier", cast(ModelPlan, object())
    )
    tool = {
        "name": "MailFieldAssessment",
        "description": "Return a source-grounded assessment.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {"m1_summary": {"type": "string"}},
                "required": ["m1_summary"],
            }
        },
    }
    request = model.format_request(
        [{"role": "user", "content": [{"text": "합성 메일 원문"}]}],
        [tool],
        [{"text": "원문을 검증해요."}],
    )
    assert request["modelId"] == model_id
    assert (
        request["toolConfig"]["tools"][0]["toolSpec"]["name"] == "MailFieldAssessment"
    )
    if model_id == DEFAULT_BEDROCK_MODEL_ID:
        assert request["inferenceConfig"]["maxTokens"] == 10000
        assert request["additionalModelRequestFields"] == {
            "reasoningConfig": {"type": "enabled", "maxReasoningEffort": "low"}
        }
    else:
        assert request["inferenceConfig"]["maxTokens"] == 4096
        assert "additionalModelRequestFields" not in request
