from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from quietpilot_agent import InMemoryContextRepository
from quietpilot_agent.context import InvocationAudit
from quietpilot_agent.discovery import (
    _log_discovery_rejection,
    discover_action_ready_candidate,
    discover_action_ready_candidates,
)
from quietpilot_agent.local_model import DeterministicModel, ModelPlan
from quietpilot_agent.models import (
    CapabilityRecord,
    CapabilityStatus,
    CaseType,
    DiscoveryAssessment,
    DiscoveryBatchAssessment,
    EvidenceRecord,
    OrchestrationRequest,
    Risk,
)
from quietpilot_agent.repair import StructuredOutputRepairGuard


def _inputs(title="과제 제출 마감"):
    request = OrchestrationRequest(
        user_id="synthetic-user",
        case_type=CaseType.CONNECTED_SIGNAL,
        goal="제출 준비",
        evidence_refs=["mail:synthetic-deadline"],
        capability_ids=["quietpilot.task.prepare"],
        primary_group_hint="deadlines",
        risk=Risk.LOW,
    )
    repository = InMemoryContextRepository(
        evidence=[
            EvidenceRecord(
                user_id=request.user_id,
                ref=request.evidence_refs[0],
                revision=1,
                source="gmail",
                title=title,
                untrusted_text="Synthetic source body must not appear in diagnostics.",
            )
        ],
        capabilities=[
            CapabilityRecord(
                user_id=request.user_id,
                capability_id=request.capability_ids[0],
                connector="quietpilot",
                status=CapabilityStatus.AVAILABLE,
                operations=["quietpilot.prepare_task"],
                required_scopes=[],
            )
        ],
    )
    return request, repository.open_scope(request)


def _change_opportunity(output, *, source_ref=None, null_field=None):
    opportunity = (
        output.opportunities[0]
        if isinstance(output, DiscoveryBatchAssessment)
        else output.opportunity
    )
    assert opportunity is not None
    if source_ref is not None or null_field == "required_scopes":
        action = opportunity.proposed_actions[0]
        changes = (
            {"parameters": {**action.parameters, "source_ref": source_ref}}
            if source_ref is not None
            else {"required_scopes": None}
        )
        opportunity = opportunity.model_copy(
            update={"proposed_actions": [action.model_copy(update=changes)]}
        )
    if null_field == "tags":
        opportunity = opportunity.model_copy(update={"tags": None})
    if isinstance(output, DiscoveryBatchAssessment):
        return output.model_copy(update={"opportunities": [opportunity]})
    return output.model_copy(update={"opportunity": opportunity})


class _InspectingModel(DeterministicModel):
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.advertised_specs = deepcopy(tool_specs)
        async for event in super().stream(
            messages, tool_specs, system_prompt, **kwargs
        ):
            yield event


class _FeedbackFactory:
    def __init__(self, code: str, *, stay_invalid: bool = False):
        self.code = code
        self.stay_invalid = stay_invalid
        self.models: list[_InspectingModel] = []

    def create(self, role: str, plan: ModelPlan):
        output = plan.output
        invalid_attempts = 0
        if role == "discovery_batch_planner":
            assert isinstance(output, DiscoveryBatchAssessment)
            if self.code == "action_source_ref":
                output = _change_opportunity(output, source_ref="foreign:synthetic")
            elif self.code == "batch_coverage":
                output = output.model_copy(
                    update={"assessed_evidence_refs": ["foreign:synthetic"]}
                )
            elif self.code == "schema_validation":
                invalid_attempts = 2
            else:
                raise AssertionError("Unexpected synthetic failure")
        elif self.stay_invalid:
            output = _change_opportunity(output, source_ref="foreign:synthetic")
        model = _InspectingModel(
            role,
            ModelPlan(
                steps=plan.steps,
                output=output,
                invalid_output_attempts=invalid_attempts,
            ),
        )
        self.models.append(model)
        return model


def _first_prompt(model):
    return json.loads(model.received_messages[0][0]["content"][0]["text"])


@pytest.mark.parametrize(
    "code", ["action_source_ref", "batch_coverage", "schema_validation"]
)
def test_batch_rejection_code_reaches_first_individual_sdk_prompt(code, capsys):
    request, scope = _inputs()
    factory = _FeedbackFactory(code)
    result = discover_action_ready_candidates(request, scope, factory)

    assert result.unresolved_evidence_count == 0
    assert len(result.candidates) == 1
    assert len(factory.models) == 2
    feedback = _first_prompt(factory.models[1])["previous_rejection"]
    assert feedback["code"] == code
    assert isinstance(feedback["guidance"], str) and feedback["guidance"]
    if code == "action_source_ref":
        assert feedback == {
            "code": "action_source_ref",
            "guidance": (
                "Copy one exact opportunity evidence_ref into every action parameters.source_ref."
            ),
        }
    action = result.candidates[0].proposed_actions[0]
    assert action.parameters["source_ref"] == request.evidence_refs[0]
    assert result.external_mutation_count == 0
    logs = capsys.readouterr().out
    assert "foreign:synthetic" not in logs
    assert "Synthetic source body" not in logs


def test_unrepaired_source_ref_remains_unresolved_with_same_bounded_attempts(capsys):
    request, scope = _inputs()
    factory = _FeedbackFactory("action_source_ref", stay_invalid=True)
    result = discover_action_ready_candidates(request, scope, factory)

    assert result.candidates == []
    assert result.unresolved_evidence_count == 1
    assert len(factory.models) == 4  # One batch and the existing three individual runs.
    assert all(model.output_attempts == 2 for model in factory.models)
    assert all(
        _first_prompt(model)["previous_rejection"]["code"] == "action_source_ref"
        for model in factory.models[1:]
    )
    assert result.external_mutation_count == 0
    logs = capsys.readouterr().out
    assert '"unresolved_count":1' in logs
    assert "foreign:synthetic" not in logs


def test_individual_suppression_keeps_nullable_opportunity_and_original_tool_name():
    request, scope = _inputs("소식 안내")
    factory = _FeedbackFactory("schema_validation")
    result = discover_action_ready_candidates(request, scope, factory)

    assert result.candidates == []
    assert result.unresolved_evidence_count == 0
    assert len(factory.models) == 2
    model = factory.models[1]
    assert type(model.plan.output) is DiscoveryAssessment
    assert model.plan.output.opportunity is None
    assert model.output_attempts == 1
    assert "DiscoveryAssessment" in model.tool_calls
    spec = next(s for s in model.advertised_specs if s["name"] == "DiscoveryAssessment")
    schema = spec["inputSchema"]["json"]
    opportunity = schema["properties"]["opportunity"]
    assert opportunity["type"] == ["object", "null"]
    assert opportunity["properties"]["tags"]["type"] == "array"
    assert (
        opportunity["properties"]["proposed_actions"]["items"]["properties"][
            "required_scopes"
        ]["type"]
        == "array"
    )


class _NullableFactory:
    def __init__(self, field: str, *, repair: bool):
        self.field = field
        self.repair = repair
        self.models: list[_InspectingModel] = []

    def create(self, role: str, plan: ModelPlan):
        good = plan.output
        if self.field == "opportunities" and isinstance(good, DiscoveryBatchAssessment):
            bad = good.model_copy(update={"opportunities": None})
        else:
            bad = _change_opportunity(
                good,
                null_field="tags" if self.field == "opportunities" else self.field,
            )
        repair = self.repair

        class _NullableModel(_InspectingModel):
            async def stream(
                self, messages, tool_specs=None, system_prompt=None, **kwargs
            ):
                self.plan = ModelPlan(
                    steps=plan.steps,
                    output=good if repair and self.output_attempts else bad,
                )
                async for event in super().stream(
                    messages, tool_specs, system_prompt, **kwargs
                ):
                    yield event

        model = _NullableModel(role, plan)
        self.models.append(model)
        return model


@pytest.mark.parametrize("field", ["opportunities", "tags", "required_scopes"])
def test_sdk_advertises_nonnullable_arrays_and_accepts_only_explicit_array_repair(
    field,
):
    request, scope = _inputs()
    factory = _NullableFactory(field, repair=True)
    result = discover_action_ready_candidates(request, scope, factory)

    assert result.unresolved_evidence_count == 0
    assert len(result.candidates) == 1
    assert len(factory.models) == 1
    model = factory.models[0]
    assert model.output_attempts == 2
    spec = next(
        s for s in model.advertised_specs if s["name"] == "DiscoveryBatchAssessment"
    )
    schema = spec["inputSchema"]["json"]
    opportunity = schema["properties"]["opportunities"]["items"]
    action = opportunity["properties"]["proposed_actions"]["items"]
    for parent, name in [
        (schema, "opportunities"),
        (opportunity, "tags"),
        (action, "required_scopes"),
    ]:
        assert parent["properties"][name]["type"] == "array"
        assert name in parent["required"]
    assert result.candidates[0].proposed_actions[0].required_scopes == []


@pytest.mark.parametrize("field", ["opportunities", "tags", "required_scopes"])
def test_null_arrays_never_become_empty_success_or_default_actions(field):
    request, scope = _inputs()
    factory = _NullableFactory(field, repair=False)
    result = discover_action_ready_candidates(request, scope, factory)

    assert result.candidates == []
    assert result.unresolved_evidence_count == 1
    assert len(factory.models) == 4
    assert all(model.output_attempts == 2 for model in factory.models)
    assert result.external_mutation_count == 0


class _RelationFactory:
    def __init__(self, kind, *, repair=True):
        self.kind = kind
        self.repair = repair
        self.models = []

    def create(self, role, plan):
        original = plan.output
        opportunity = (
            original.opportunities[0]
            if isinstance(original, DiscoveryBatchAssessment)
            else original.opportunity
        )
        assert opportunity is not None
        action = opportunity.proposed_actions[0].model_copy(update={"risk": Risk.HIGH})
        good_opportunity = opportunity.model_copy(
            update={"risk": Risk.HIGH, "proposed_actions": [action]}
        )
        bad_action = action.model_copy(
            update={
                "risk": Risk.LOW if self.kind in {"risk", "both"} else Risk.HIGH,
                "parameters": {
                    **action.parameters,
                    "source_ref": "synthetic-private-foreign-ref",
                }
                if self.kind in {"source", "both"}
                else action.parameters,
            }
        )
        bad_opportunity = good_opportunity.model_copy(
            update={"proposed_actions": [bad_action]}
        )
        if isinstance(original, DiscoveryBatchAssessment):
            good = original.model_copy(update={"opportunities": [good_opportunity]})
            bad = original.model_copy(update={"opportunities": [bad_opportunity]})
        else:
            good = original.model_copy(update={"opportunity": good_opportunity})
            bad = original.model_copy(update={"opportunity": bad_opportunity})
        repair = self.repair

        class _RelationModel(_InspectingModel):
            async def stream(
                self, messages, tool_specs=None, system_prompt=None, **kwargs
            ):
                self.plan = ModelPlan(
                    steps=plan.steps,
                    output=good if repair and self.output_attempts else bad,
                )
                async for event in super().stream(
                    messages, tool_specs, system_prompt, **kwargs
                ):
                    yield event

        model = _RelationModel(role, plan)
        self.models.append(model)
        return model


@pytest.mark.parametrize("mode", ["batch", "individual"])
def test_sdk_advertises_required_current_source_refs_and_retains_relation_repair(mode):
    request, scope = _inputs()
    # Match the actual per-evidence fallback: the repository scope can be wider
    # than this individual request, but its advertised choices must not be.
    sibling = scope.evidence[request.evidence_refs[0]].model_copy(
        update={"ref": "mail:synthetic-sibling"}
    )
    wider_request = request.model_copy(
        update={"evidence_refs": [*request.evidence_refs, sibling.ref]}
    )
    wider_scope = InMemoryContextRepository(
        evidence=[*scope.evidence.values(), sibling],
        capabilities=list(scope.capabilities.values()),
    ).open_scope(wider_request)
    factory = _RelationFactory("source")
    run = (
        discover_action_ready_candidates
        if mode == "batch"
        else discover_action_ready_candidate
    )
    result = run(request, wider_scope, factory)
    assert result.validated
    assert len(factory.models) == 1
    model = factory.models[0]
    name = "DiscoveryBatchAssessment" if mode == "batch" else "DiscoveryAssessment"
    spec = next(spec for spec in model.advertised_specs if spec["name"] == name)
    properties = spec["inputSchema"]["json"]["properties"]
    opportunity = (
        properties["opportunities"]["items"]
        if mode == "batch"
        else properties["opportunity"]
    )
    parameters = opportunity["properties"]["proposed_actions"]["items"]["properties"][
        "parameters"
    ]
    assert parameters.get("required") == ["source_ref"]
    assert parameters.get("properties", {}).get("source_ref") == {
        "type": "string",
        "enum": request.evidence_refs,
    }
    # A scripted invalid value is still rejected and explicitly repaired by the
    # real SDK; advertising choices must not auto-rewrite or accept bad output.
    assert model.output_attempts == 2
    candidate = result.candidates[0] if mode == "batch" else result.candidate
    assert (
        candidate.proposed_actions[0].parameters["source_ref"]
        == request.evidence_refs[0]
    )
    assert sibling.ref not in parameters["properties"]["source_ref"]["enum"]


@pytest.mark.parametrize("mode", ["batch", "individual"])
@pytest.mark.parametrize("kind", ["risk", "source", "both"])
def test_sdk_repairs_action_relations_in_same_context_with_exact_static_paths(
    mode, kind
):
    request, scope = _inputs()
    factory = _RelationFactory(kind)
    run = (
        discover_action_ready_candidates
        if mode == "batch"
        else discover_action_ready_candidate
    )
    result = run(request, scope, factory)
    candidates = result.candidates if mode == "batch" else [result.candidate]
    assert result.validated and len(candidates) == 1
    assert len(factory.models) == 1
    model = factory.models[0]
    assert model.output_attempts == 2
    assert model.tool_calls.count("read_evidence_context") == 1
    assert model.tool_calls.count("read_capability_context") == 1
    candidate = candidates[0]
    assert candidate.risk is Risk.HIGH
    assert candidate.proposed_actions[0].risk is Risk.HIGH
    assert (
        candidate.proposed_actions[0].parameters["source_ref"]
        == request.evidence_refs[0]
    )
    error_text = "\n".join(
        item["text"]
        for message in model.received_messages[-1]
        for block in message["content"]
        if (tool_result := block.get("toolResult", {})).get("status") == "error"
        for item in tool_result["content"]
        if "text" in item
    )
    prefix = "opportunities -> 0" if mode == "batch" else "opportunity"
    if kind in {"risk", "both"}:
        assert f"{prefix} -> proposed_actions -> 0 -> risk" in error_text
        assert "action_risk:" in error_text
        assert "not email urgency or incident severity" in error_text
    if kind in {"source", "both"}:
        assert (
            f"{prefix} -> proposed_actions -> 0 -> parameters -> source_ref"
            in error_text
        )
        assert "action_source_ref:" in error_text
    assert "synthetic-private-foreign-ref" not in error_text
    assert "Synthetic source body" not in error_text
    assert all(
        "Risk describes the consequences" in prompt
        for prompt in model.received_system_prompts
    )


def test_persistent_sdk_relation_errors_fail_closed_and_report_only_paths(capsys):
    request, scope = _inputs()
    factory = _RelationFactory("both", repair=False)
    result = discover_action_ready_candidates(request, scope, factory)
    assert result.candidates == [] and result.unresolved_evidence_count == 1
    assert len(factory.models) == 4
    assert all(model.output_attempts == 2 for model in factory.models)
    logs = capsys.readouterr().out
    diagnostics = [
        row
        for line in logs.splitlines()
        if (row := json.loads(line))["event"] == "discovery_rejected"
    ]
    assert len(diagnostics) == 4
    assert all(
        row["rejected_rules"] == ["action_risk", "action_source_ref"]
        for row in diagnostics
    )
    assert all(row["rejection_code"] == "action_constraints" for row in diagnostics)
    assert all(
        row["output_failures"] == 2 and row["tool_validation_errors"]
        for row in diagnostics
    )
    assert "synthetic-private-foreign-ref" not in logs
    assert "Synthetic source body" not in logs


def test_actual_sdk_missing_output_is_distinct_from_rejected_fields(capsys):
    request, scope = _inputs()

    class _MissingOutputModel(_InspectingModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            if self.stream_calls < len(self.plan.steps):
                async for event in super().stream(
                    messages, tool_specs, system_prompt, **kwargs
                ):
                    yield event
                return
            self.stream_calls += 1
            yield {"messageStart": {"role": "assistant"}}
            yield {"messageStop": {"stopReason": "malformed_tool_use"}}

    class _Factory:
        def create(self, role, plan):
            return (
                _MissingOutputModel(role, plan)
                if role == "discovery_batch_planner"
                else _InspectingModel(role, plan)
            )

    result = discover_action_ready_candidates(request, scope, _Factory())
    assert len(result.candidates) == 1 and result.unresolved_evidence_count == 0
    diagnostic = json.loads(capsys.readouterr().out.splitlines()[0])
    assert diagnostic["output_shape"] == "none"
    assert diagnostic["stop_reason"] == "malformed_tool_use"
    assert diagnostic["output_attempts"] == diagnostic["output_failures"] == 0
    assert diagnostic["tool_validation_errors"] == []
    assert diagnostic["validation_errors"] == [{"loc": [], "type": "model_type"}]


def test_outer_schema_diagnostics_never_log_unknown_keys_values_or_provider_text(
    capsys,
):
    private = "SYNTHETIC_PRIVATE_DIAGNOSTIC_MARKER"
    with pytest.raises(ValidationError) as captured:
        DiscoveryBatchAssessment.model_validate({private: private})
    _log_discovery_rejection(
        mode="batch",
        error=captured.value,
        rejection_code="schema_validation",
        repair_guard=StructuredOutputRepairGuard(),
        audit=InvocationAudit(expected_specialists=()),
        result=SimpleNamespace(
            structured_output={private: private}, stop_reason=private
        ),
    )
    output = capsys.readouterr().out
    diagnostic = json.loads(output)
    assert private not in output
    assert diagnostic["stop_reason"] == "unknown"
    assert diagnostic["output_shape"] == "object"
    assert {"loc": ["unknown_field"], "type": "extra_forbidden"} in diagnostic[
        "validation_errors"
    ]
