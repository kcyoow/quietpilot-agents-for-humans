"""Production Strands model factory for Amazon Bedrock Converse."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from strands.models import BedrockModel
from strands.models.model import Model

from .local_model import ModelPlan

DEFAULT_BEDROCK_MODEL_ID = "global.amazon.nova-2-lite-v1:0"
DEFAULT_BEDROCK_REGION = "ap-northeast-2"
MODEL_ID_ENV = "QUIETPILOT_BEDROCK_MODEL_ID"
MODEL_REGION_ENV = "QUIETPILOT_BEDROCK_REGION"


@dataclass(frozen=True, slots=True)
class BedrockModelFactory:
    """Create independent Bedrock-backed models for each agent role."""

    mail_batch_size: ClassVar[int] = 1
    mail_concurrency: ClassVar[int] = 4

    model_id: str = DEFAULT_BEDROCK_MODEL_ID
    region: str = DEFAULT_BEDROCK_REGION

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        if not self.region.strip():
            raise ValueError("region must be non-empty")

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> BedrockModelFactory:
        values = os.environ if environment is None else environment
        return cls(
            model_id=values.get(MODEL_ID_ENV, DEFAULT_BEDROCK_MODEL_ID),
            region=values.get(MODEL_REGION_ENV, DEFAULT_BEDROCK_REGION),
        )

    def create(self, role: str, plan: ModelPlan) -> Model:
        """Build a fresh client without using deterministic test-plan contents."""

        del plan
        # Reserve enough output space for the mail assessment's structured tool
        # response rather than relying on the provider's dynamic output default.
        mail_options = (
            {"max_tokens": 4096}
            if role
            in {"mail_interest_tags", "mail_interest_matcher", "mail_interest_verifier"}
            else {}
        )
        if role == "mail_interest_verifier" and self.model_id.endswith(
            "amazon.nova-2-lite-v1:0"
        ):
            # Source relevance and factual qualifiers require more than schema
            # compliance. Reserve room for bounded Nova reasoning and final copy.
            mail_options.update(
                max_tokens=10000,
                additional_request_fields={
                    "reasoningConfig": {
                        "type": "enabled",
                        "maxReasoningEffort": "low",
                    }
                },
            )
        return BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            temperature=0.0,
            **mail_options,
        )
