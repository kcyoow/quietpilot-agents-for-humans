from types import SimpleNamespace

import pytest
from quietpilot_agent.repair import (
    StructuredOutputRepairGuard,
    StructuredOutputValidationDiagnostics,
)


def test_validation_diagnostics_preserve_rejection_without_values_or_error_messages():
    import json

    from pydantic import Field, ValidationError, create_model, model_validator

    diagnostics = StructuredOutputValidationDiagnostics(frozenset({"content"}))

    def reject(value):
        raise ValueError("PRIVATE-EXCEPTION-" + value.content)

    output = create_model(
        "DiagnosticOutput",
        content=(str, Field()),
        __validators__={
            "reject": model_validator(mode="after")(reject),
            "diagnostics": model_validator(mode="wrap")(
                lambda value, handler: diagnostics.observe(value, handler)
            ),
        },
    )
    for value in [{"content": "PRIVATE-SOURCE"}, {}]:
        with pytest.raises(ValidationError):
            output.model_validate(value)
    assert {item["type"] for item in diagnostics.errors} == {"value_error", "missing"}
    assert diagnostics.errors[1]["fields"] == ["content"]
    assert "PRIVATE" not in json.dumps(diagnostics.errors)


def tool_cycle(guard, cycle, *, success=False):
    tool = SimpleNamespace(tool_type="structured_output")
    before = SimpleNamespace(
        selected_tool=tool,
        invocation_state={"event_loop_cycle_id": cycle},
        cancel_tool=None,
    )
    guard._before_tool_call(before)
    guard._after_tool_call(
        SimpleNamespace(
            selected_tool=tool,
            cancel_message=before.cancel_tool,
            result={"status": "success" if success else "error"},
        )
    )
    after = SimpleNamespace(end_turn=None)
    guard._after_tools(after)
    return before.cancel_tool, after.end_turn


def test_default_stops_on_second_failure_and_rejects_third_attempt():
    guard = StructuredOutputRepairGuard()
    assert guard.max_attempts == 2
    assert tool_cycle(guard, "first") == (None, None)
    assert tool_cycle(guard, "second") == (None, "AGENT_OUTPUT_INVALID")
    assert tool_cycle(guard, "third", success=True) == (
        "AGENT_OUTPUT_INVALID",
        "AGENT_OUTPUT_INVALID",
    )
    assert guard.failures == 2


def test_four_attempt_budget_allows_three_failures_then_success():
    guard = StructuredOutputRepairGuard(max_attempts=4)
    for index in range(3):
        assert tool_cycle(guard, str(index)) == (None, None)
    assert tool_cycle(guard, "success", success=True) == (None, None)
    assert guard.attempts == 4 and guard.failures == 3
    assert not guard.protocol_violation


def test_fourth_failure_stops_and_fifth_attempt_is_cancelled():
    guard = StructuredOutputRepairGuard(max_attempts=4)
    for index in range(3):
        assert tool_cycle(guard, str(index)) == (None, None)
    assert tool_cycle(guard, "fourth") == (None, "AGENT_OUTPUT_INVALID")
    assert guard.failures == 4
    assert tool_cycle(guard, "fifth", success=True) == (
        "AGENT_OUTPUT_INVALID",
        "AGENT_OUTPUT_INVALID",
    )


def test_duplicate_cycle_remains_invalid_with_four_attempt_budget():
    guard = StructuredOutputRepairGuard(max_attempts=4)
    assert tool_cycle(guard, "same-cycle") == (None, None)
    assert tool_cycle(guard, "same-cycle", success=True) == (
        "AGENT_OUTPUT_INVALID",
        "AGENT_OUTPUT_INVALID",
    )
    assert guard.protocol_violation and guard.failures == 1


def test_existing_positional_fields_keep_their_meaning():
    cycles = ["previous"]
    guard = StructuredOutputRepairGuard(1, 0, 1, False, cycles)
    assert guard.attempts == 1 and guard.missing_output_failures == 1
    assert guard._attempt_cycles is cycles
    assert guard.max_attempts == 2


@pytest.mark.parametrize("value", [1, 2, 3, 4])
def test_supported_integer_bounds_are_accepted(value):
    assert StructuredOutputRepairGuard(max_attempts=value).max_attempts == value


@pytest.mark.parametrize("value", [True, False, 1.0, 4.0, "4", None])
def test_non_integer_or_boolean_budget_is_rejected(value):
    with pytest.raises(TypeError, match="strict integer"):
        StructuredOutputRepairGuard(max_attempts=value)


@pytest.mark.parametrize("value", [-1, 0, 5, 100])
def test_integer_budget_outside_supported_range_is_rejected(value):
    with pytest.raises(ValueError, match="between one and four"):
        StructuredOutputRepairGuard(max_attempts=value)
