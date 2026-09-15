"""Bounded repair budgets for Strands structured-output validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError
from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    AfterToolsEvent,
    BeforeToolCallEvent,
)
from strands.hooks.registry import HookRegistry

from .context import EXPECTED_SPECIALIST_SEQUENCE, InvocationAudit

SpecialistValidator = Callable[[dict[str, object]], object]


@dataclass
class StructuredOutputValidationDiagnostics:
    """Record only schema field names and validator source locations, never values."""

    field_names: frozenset[str]
    errors: list[dict[str, object]] = field(default_factory=list)

    def observe(self, value, handler):
        try:
            return handler(value)
        except ValidationError as error:
            for detail in error.errors(include_input=False, include_url=False):
                locations = []
                cause = detail.get("ctx", {}).get("error")
                trace = cause.__traceback__ if isinstance(cause, Exception) else None
                while trace is not None:
                    module = trace.tb_frame.f_globals.get("__name__", "")
                    if module.startswith("quietpilot_agent."):
                        locations.append(f"{module}:{trace.tb_lineno}")
                    trace = trace.tb_next
                item = {
                    "type": detail["type"],
                    "fields": [
                        name for name in detail["loc"] if name in self.field_names
                    ],
                    "locations": locations[-4:],
                }
                if item not in self.errors and len(self.errors) < 8:
                    self.errors.append(item)
            raise


@dataclass
class StructuredOutputRepairGuard:
    """Stop structured output at a trusted attempt budget, defaulting to two."""

    attempts: int = 0
    failures: int = 0
    missing_output_failures: int = 0
    protocol_violation: bool = False
    _attempt_cycles: list[str] | None = None
    max_attempts: int = 2

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int:
            raise TypeError("max_attempts must be a strict integer")
        if not 1 <= self.max_attempts <= 4:
            raise ValueError("max_attempts must be between one and four")

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        del kwargs
        registry.add_callback(BeforeToolCallEvent, self._before_tool_call)
        registry.add_callback(AfterModelCallEvent, self._after_model_call)
        registry.add_callback(AfterToolCallEvent, self._after_tool_call)
        registry.add_callback(AfterToolsEvent, self._after_tools)

    def _before_tool_call(self, event: BeforeToolCallEvent) -> None:
        selected_tool = event.selected_tool
        if selected_tool is None or selected_tool.tool_type != "structured_output":
            return

        if self._attempt_cycles is None:
            self._attempt_cycles = []
        cycle = str(event.invocation_state.get("event_loop_cycle_id", "unknown"))
        duplicate_cycle = cycle in self._attempt_cycles
        too_many_attempts = self.attempts >= self.max_attempts
        self.attempts += 1
        self._attempt_cycles.append(cycle)
        if duplicate_cycle or too_many_attempts:
            self.protocol_violation = True
            event.cancel_tool = "AGENT_OUTPUT_INVALID"

    def _after_tool_call(self, event: AfterToolCallEvent) -> None:
        selected_tool = event.selected_tool
        if selected_tool is None or selected_tool.tool_type != "structured_output":
            return
        if event.cancel_message is None and event.result.get("status") == "error":
            self.failures += 1

    def _after_model_call(self, event: AfterModelCallEvent) -> None:
        if (
            event.stop_response is not None
            and event.stop_response.stop_reason == "end_turn"
        ):
            self.missing_output_failures += 1

    def _after_tools(self, event: AfterToolsEvent) -> None:
        if self.failures >= self.max_attempts or self.protocol_violation:
            event.end_turn = "AGENT_OUTPUT_INVALID"


@dataclass
class SpecialistCompletionGuard:
    audit: InvocationAudit
    validators: dict[str, SpecialistValidator]
    canonical_outputs: dict[str, object]
    repair_guards: dict[str, StructuredOutputRepairGuard]

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        del kwargs
        registry.add_callback(AfterToolCallEvent, self._after_tool_call)

    def _after_tool_call(self, event: AfterToolCallEvent) -> None:
        tool_name = event.tool_use.get("name")
        if tool_name not in EXPECTED_SPECIALIST_SEQUENCE:
            return
        if event.result.get("status") != "success":
            print(
                f"quietpilot_specialist_rejected role={tool_name} reason=tool_failure"
            )
            self.audit.fail_specialist(f"specialist failed: {tool_name}")
            return

        content = event.result.get("content", [])
        json_blocks = [block["json"] for block in content if "json" in block]
        if not json_blocks and self._bounded_work_completed(tool_name):
            canonical_output = self.canonical_outputs.get(tool_name)
            if canonical_output is not None:
                print(
                    "quietpilot_specialist_fallback "
                    f"role={tool_name} reason=missing_typed_content"
                )
                self.audit.record_specialist_fallback(tool_name, canonical_output)
                return
        if len(json_blocks) != 1 or not isinstance(json_blocks[0], dict):
            print(
                f"quietpilot_specialist_rejected role={tool_name} reason=typed_content"
            )
            self.audit.fail_specialist(
                f"specialist returned invalid typed content: {tool_name}"
            )
            return
        try:
            output = self.validators[tool_name](json_blocks[0])
        except (TypeError, ValueError) as error:
            print(f"quietpilot_specialist_rejected role={tool_name} reason=grounding")
            self.audit.fail_specialist(
                f"specialist grounding failed: {tool_name}: {type(error).__name__}"
            )
            return
        self.audit.record_specialist(tool_name, output)

    def _bounded_work_completed(self, tool_name: str) -> bool:
        repair_guard = self.repair_guards.get(tool_name)
        if repair_guard is None or (
            repair_guard.failures != 0
            or repair_guard.missing_output_failures != 0
            or repair_guard.protocol_violation
            or repair_guard.attempts > 2
        ):
            return False
        required_read = {
            "signal_analyst": "read_evidence_context",
            "capability_analyst": "read_capability_context",
        }.get(tool_name)
        if required_read is None:
            return tool_name == "case_planner"
        return (
            self.audit.context_read_attempts.get(required_read) == 1
            and self.audit.context_read_counts.get(required_read) == 1
        )


@dataclass
class ToolErrorStopGuard:
    """Stop a specialist after any non-schema tool error."""

    saw_error: bool = False

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        del kwargs
        registry.add_callback(AfterToolCallEvent, self._after_tool_call)
        registry.add_callback(AfterToolsEvent, self._after_tools)

    def _after_tool_call(self, event: AfterToolCallEvent) -> None:
        selected_tool = event.selected_tool
        if selected_tool is None or selected_tool.tool_type == "structured_output":
            return
        if event.result.get("status") == "error":
            self.saw_error = True

    def _after_tools(self, event: AfterToolsEvent) -> None:
        if self.saw_error:
            event.end_turn = "CONTEXT_TOOL_ERROR"
