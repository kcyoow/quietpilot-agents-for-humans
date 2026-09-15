"""A network-free Strands model that exercises the real SDK event loop."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel
from strands.models.model import Model
from strands.types.content import Messages, SystemContentBlock
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ToolStep:
    name: str
    input: dict[str, object]


@dataclass(frozen=True)
class ModelPlan:
    steps: tuple[ToolStep, ...]
    output: BaseModel
    invalid_output_attempts: int = 0


class AgentModelFactory(Protocol):
    """Injection boundary for a future production model provider."""

    def create(self, role: str, plan: ModelPlan) -> Model: ...


class ToolUnavailableError(RuntimeError):
    pass


class DeterministicModel(Model):
    """Emit scripted tool calls without network, credentials, or provider defaults."""

    def __init__(self, role: str, plan: ModelPlan) -> None:
        self.role = role
        self.plan = plan
        self.stream_calls = 0
        self.output_attempts = 0
        self.tool_calls: list[str] = []
        self.seen_tool_names: list[frozenset[str]] = []
        self.received_messages: list[Messages] = []
        self.received_system_prompts: list[str | None] = []
        self._config: dict[str, object] = {"model_id": f"quietpilot-local:{role}"}

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, object]:
        return dict(self._config)

    async def structured_output(
        self,
        output_model: type[T],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any], None]:
        del prompt, system_prompt, kwargs
        yield {"output": output_model.model_validate(self.plan.output)}

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        system_prompt_content: list[SystemContentBlock] | None = None,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.received_messages.append(deepcopy(messages))
        self.received_system_prompts.append(system_prompt)
        del messages, system_prompt, tool_choice, system_prompt_content
        del invocation_state, kwargs

        specs = {spec["name"]: spec for spec in tool_specs or []}
        self.seen_tool_names.append(frozenset(specs))
        if self.stream_calls < len(self.plan.steps):
            step = self.plan.steps[self.stream_calls]
            tool_name = step.name
            payload = step.input
        else:
            tool_name = type(self.plan.output).__name__
            if self.output_attempts < self.plan.invalid_output_attempts:
                payload = {}
            else:
                payload = self.plan.output.model_dump(mode="json")
            self.output_attempts += 1

        self.stream_calls += 1
        if tool_name not in specs:
            raise ToolUnavailableError(
                f"tool {tool_name!r} is not registered for {self.role}"
            )

        self.tool_calls.append(tool_name)
        tool_use_id = f"{self.role}-{self.stream_calls}"
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {
                    "toolUse": {
                        "toolUseId": tool_use_id,
                        "name": tool_name,
                    }
                },
            }
        }
        yield {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"input": json.dumps(payload)}},
            }
        }
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 0},
            }
        }


class DeterministicModelFactory:
    def __init__(self) -> None:
        self.models: dict[str, DeterministicModel] = {}

    def create(self, role: str, plan: ModelPlan) -> Model:
        model = DeterministicModel(role=role, plan=plan)
        self.models[role] = model
        return model
