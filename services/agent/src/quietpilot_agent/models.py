"""Strict inputs and proposal-only outputs for QuietPilot agents."""

import json
import re
from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

MAX_IDENTIFIER_CHARS = 256
MAX_SHORT_TEXT_CHARS = 500
MAX_LONG_TEXT_CHARS = 2000
MAX_PARAMETERS_BYTES = 8192
MAX_CASE_SOURCE_TEXT_CHARS = 16_000


class Risk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CaseType(StrEnum):
    CONNECTED_SIGNAL = "CONNECTED_SIGNAL"
    DIRECT_DELEGATION = "DIRECT_DELEGATION"
    ROUTINE_DISCOVERY = "ROUTINE_DISCOVERY"
    EXCEPTION_APPROVAL = "EXCEPTION_APPROVAL"


class CapabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    INACCESSIBLE = "INACCESSIBLE"
    MISSING = "MISSING"


class OrchestrationStatus(StrEnum):
    PROPOSED = "PROPOSED"
    SUPPRESSED = "SUPPRESSED"
    EXCEPTION_REQUIRED = "EXCEPTION_REQUIRED"


class ProposalStage(StrEnum):
    DISCOVERY = "DISCOVERY"
    CASE_PLANNING = "CASE_PLANNING"


class DiscoveryDisposition(StrEnum):
    PROPOSE = "PROPOSE"
    SUPPRESS = "SUPPRESS"


class OpportunityType(StrEnum):
    APPOINTMENT = "APPOINTMENT"
    DEADLINE = "DEADLINE"
    FOLLOW_UP = "FOLLOW_UP"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def risk_at_least(actual: Risk, minimum: Risk) -> bool:
    order = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2}
    return order[actual] >= order[minimum]


def _validate_text_list(
    values: list[str],
    *,
    field_name: str,
    max_items: int,
    max_chars: int,
) -> list[str]:
    if len(values) > max_items:
        raise ValueError(f"{field_name} exceeds {max_items} items")
    for value in values:
        if len(value) > max_chars:
            raise ValueError(f"{field_name} item exceeds {max_chars} characters")
    return values


class EvidenceRecord(StrictModel):
    """Redacted evidence owned by exactly one authenticated user."""

    user_id: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    revision: StrictInt = Field(ge=1)
    source: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    facts: list[str] = Field(default_factory=list, max_length=12)
    untrusted_text: str | None = Field(default=None, max_length=4000)

    @field_validator("user_id", "ref", "source", "title")
    @classmethod
    def bound_identity_text(cls, value: str, info: ValidationInfo) -> str:
        limit = (
            MAX_SHORT_TEXT_CHARS if info.field_name == "title" else MAX_IDENTIFIER_CHARS
        )
        if len(value) > limit:
            raise ValueError(f"{info.field_name} exceeds {limit} characters")
        return value

    @field_validator("facts")
    @classmethod
    def bound_facts(cls, values: list[str]) -> list[str]:
        return _validate_text_list(
            values,
            field_name="facts",
            max_items=12,
            max_chars=MAX_SHORT_TEXT_CHARS,
        )


class ResolvedGmailCaseEvidence(EvidenceRecord):
    """Transient full source fetched for an owned, selected Case, never scan input."""

    source: Literal["gmail"] = "gmail"
    untrusted_text: str | None = Field(
        default=None, max_length=MAX_CASE_SOURCE_TEXT_CHARS
    )


class CapabilityRecord(StrictModel):
    """Read-only inventory; it grants no authority by itself."""

    user_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    connector: str = Field(min_length=1)
    status: CapabilityStatus
    operations: list[str] = Field(default_factory=list, max_length=20)
    required_scopes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("user_id", "capability_id", "connector")
    @classmethod
    def bound_identity_text(cls, value: str, info: ValidationInfo) -> str:
        if len(value) > MAX_IDENTIFIER_CHARS:
            raise ValueError(
                f"{info.field_name} exceeds {MAX_IDENTIFIER_CHARS} characters"
            )
        return value

    @field_validator("operations", "required_scopes")
    @classmethod
    def bound_capability_lists(
        cls, values: list[str], info: ValidationInfo
    ) -> list[str]:
        return _validate_text_list(
            values,
            field_name=info.field_name,
            max_items=20,
            max_chars=MAX_IDENTIFIER_CHARS,
        )


class OrchestrationRequest(StrictModel):
    """Trusted invocation metadata plus references to separately stored evidence."""

    user_id: str = Field(min_length=1)
    case_type: CaseType
    proposal_stage: ProposalStage = ProposalStage.DISCOVERY
    goal: str = Field(min_length=1, max_length=300)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)
    capability_ids: list[str] = Field(default_factory=list, max_length=8)
    primary_group_hint: str = Field(min_length=1, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=12)
    risk: Risk
    requested_actions: list["ActionProposal"] = Field(
        default_factory=list, max_length=8
    )
    conflicting_evidence: StrictBool = False

    @field_validator("user_id")
    @classmethod
    def bound_user_id(cls, value: str) -> str:
        if len(value) > MAX_IDENTIFIER_CHARS:
            raise ValueError(f"user_id exceeds {MAX_IDENTIFIER_CHARS} characters")
        return value

    @field_validator("evidence_refs", "capability_ids", "tags")
    @classmethod
    def bound_reference_lists(
        cls, values: list[str], info: ValidationInfo
    ) -> list[str]:
        limits = {"evidence_refs": 8, "capability_ids": 8, "tags": 12}
        return _validate_text_list(
            values,
            field_name=info.field_name,
            max_items=limits[info.field_name],
            max_chars=MAX_IDENTIFIER_CHARS,
        )


class CandidateProposal(StrictModel):
    outcome: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    opportunity_type: OpportunityType
    evidence_refs: list[str] = Field(min_length=1)
    confidence: StrictFloat = Field(ge=0, le=1)
    uncertainty_reason: str | None = None
    primary_group_hint: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    risk: Risk
    required_capabilities: list[str] = Field(min_length=1)
    proposed_actions: list["ActionProposal"] = Field(min_length=1, max_length=3)
    fingerprint_inputs: list[str] = Field(min_length=1)

    @field_validator(
        "outcome",
        "summary",
        "why_now",
        "uncertainty_reason",
        "primary_group_hint",
    )
    @classmethod
    def bound_candidate_text(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        if value is None:
            return value
        limits = {
            "outcome": MAX_SHORT_TEXT_CHARS,
            "summary": MAX_LONG_TEXT_CHARS,
            "why_now": MAX_LONG_TEXT_CHARS,
            "uncertainty_reason": MAX_LONG_TEXT_CHARS,
            "primary_group_hint": MAX_IDENTIFIER_CHARS,
        }
        limit = limits[info.field_name]
        if len(value) > limit:
            raise ValueError(f"{info.field_name} exceeds {limit} characters")
        return value

    @field_validator(
        "evidence_refs",
        "tags",
        "required_capabilities",
        "fingerprint_inputs",
    )
    @classmethod
    def bound_candidate_lists(
        cls, values: list[str], info: ValidationInfo
    ) -> list[str]:
        limits = {
            "evidence_refs": 8,
            "tags": 12,
            "required_capabilities": 8,
            "fingerprint_inputs": 16,
        }
        return _validate_text_list(
            values,
            field_name=info.field_name,
            max_items=limits[info.field_name],
            max_chars=MAX_SHORT_TEXT_CHARS,
        )

    @model_validator(mode="after")
    def require_action_ready_candidate(self) -> "CandidateProposal":
        if not 0.7 <= self.confidence <= 1:
            raise ValueError("visible candidate confidence must be at least 0.7")
        if any(
            not risk_at_least(action.risk, self.risk)
            for action in self.proposed_actions
        ):
            raise ValueError("candidate risk cannot exceed a proposed action risk")
        return self


class ActionProposal(StrictModel):
    connector: str = Field(min_length=1)
    target_resource: str = Field(min_length=1)
    verb: str = Field(min_length=1)
    parameters: dict[str, object]
    required_scopes: list[str] = Field(default_factory=list)
    risk: Risk
    reversible: StrictBool
    verification_method: str = Field(min_length=1)

    @field_validator("connector", "target_resource", "verb", "verification_method")
    @classmethod
    def bound_action_text(cls, value: str, info: ValidationInfo) -> str:
        limits = {
            "connector": 80,
            "target_resource": MAX_SHORT_TEXT_CHARS,
            "verb": 120,
            "verification_method": MAX_SHORT_TEXT_CHARS,
        }
        limit = limits[info.field_name]
        if len(value) > limit:
            raise ValueError(f"{info.field_name} exceeds {limit} characters")
        return value

    @field_validator("required_scopes")
    @classmethod
    def bound_required_scopes(cls, values: list[str]) -> list[str]:
        return _validate_text_list(
            values,
            field_name="required_scopes",
            max_items=20,
            max_chars=MAX_IDENTIFIER_CHARS,
        )

    @field_validator("parameters")
    @classmethod
    def reject_authority_and_secret_fields(
        cls, parameters: dict[str, object]
    ) -> dict[str, object]:
        try:
            encoded = json.dumps(
                parameters, ensure_ascii=False, sort_keys=True
            ).encode()
        except (TypeError, ValueError) as error:
            raise ValueError("parameters must be JSON serializable") from error
        if len(encoded) > MAX_PARAMETERS_BYTES:
            raise ValueError(
                f"parameters exceed the {MAX_PARAMETERS_BYTES}-byte budget"
            )

        forbidden = {
            "approved",
            "approval",
            "authorization",
            "credentials",
            "grant",
            "grantmode",
            "oauthtoken",
            "accesstoken",
            "refreshtoken",
            "secret",
            "userid",
        }

        def visit(value: object) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
                    if normalized_key in forbidden:
                        raise ValueError(
                            f"authority or secret field is forbidden in parameters: {key}"
                        )
                    visit(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    visit(nested)

        visit(parameters)
        return parameters


class DiscoveredOpportunity(StrictModel):
    outcome: str = Field(min_length=1, max_length=MAX_SHORT_TEXT_CHARS)
    summary: str = Field(min_length=1, max_length=MAX_LONG_TEXT_CHARS)
    why_now: str = Field(min_length=1, max_length=MAX_LONG_TEXT_CHARS)
    opportunity_type: OpportunityType
    evidence_refs: list[str] = Field(min_length=1, max_length=8)
    confidence: StrictFloat = Field(ge=0, le=1)
    primary_group_hint: str = Field(min_length=1, max_length=MAX_IDENTIFIER_CHARS)
    tags: list[str] = Field(default_factory=list, max_length=12)
    risk: Risk
    proposed_actions: list[ActionProposal] = Field(min_length=1, max_length=3)

    @field_validator("evidence_refs", "tags")
    @classmethod
    def bound_discovery_lists(
        cls, values: list[str], info: ValidationInfo
    ) -> list[str]:
        return _validate_text_list(
            values,
            field_name=info.field_name,
            max_items=8 if info.field_name == "evidence_refs" else 12,
            max_chars=MAX_SHORT_TEXT_CHARS,
        )


class DiscoveryAssessment(StrictModel):
    disposition: DiscoveryDisposition
    reason: str = Field(min_length=1, max_length=MAX_LONG_TEXT_CHARS)
    opportunity: DiscoveredOpportunity | None = None

    @model_validator(mode="after")
    def require_decision_shape(self) -> "DiscoveryAssessment":
        if self.disposition is DiscoveryDisposition.PROPOSE:
            if self.opportunity is None:
                raise ValueError("PROPOSE requires one grounded opportunity")
        elif self.opportunity is not None:
            raise ValueError("SUPPRESS cannot contain a user-visible opportunity")
        return self


class DiscoveryBatchAssessment(StrictModel):
    assessed_evidence_refs: list[str] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=1, max_length=MAX_LONG_TEXT_CHARS)
    opportunities: list[DiscoveredOpportunity] = Field(
        default_factory=list, max_length=8
    )

    @field_validator("assessed_evidence_refs")
    @classmethod
    def bound_assessed_refs(cls, values: list[str]) -> list[str]:
        return _validate_text_list(
            values,
            field_name="assessed_evidence_refs",
            max_items=8,
            max_chars=MAX_IDENTIFIER_CHARS,
        )


class CasePlanProposal(StrictModel):
    case_type: CaseType
    goal: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    evidence_revisions: dict[str, StrictInt]
    preparation_steps: list[str] = Field(default_factory=list)
    actions: list[ActionProposal] = Field(default_factory=list)
    decision_question: str = Field(min_length=1)

    @field_validator("goal", "explanation", "decision_question")
    @classmethod
    def bound_plan_text(cls, value: str, info: ValidationInfo) -> str:
        limits = {
            "goal": MAX_SHORT_TEXT_CHARS,
            "explanation": MAX_LONG_TEXT_CHARS,
            "decision_question": MAX_LONG_TEXT_CHARS,
        }
        limit = limits[info.field_name]
        if len(value) > limit:
            raise ValueError(f"{info.field_name} exceeds {limit} characters")
        return value

    @field_validator("evidence_revisions")
    @classmethod
    def bound_evidence_revisions(
        cls, revisions: dict[str, StrictInt]
    ) -> dict[str, StrictInt]:
        if len(revisions) > 8:
            raise ValueError("evidence_revisions exceeds 8 items")
        if any(len(ref) > MAX_IDENTIFIER_CHARS for ref in revisions):
            raise ValueError("evidence revision key exceeds the character budget")
        return revisions

    @field_validator("preparation_steps")
    @classmethod
    def bound_preparation_steps(cls, values: list[str]) -> list[str]:
        return _validate_text_list(
            values,
            field_name="preparation_steps",
            max_items=20,
            max_chars=MAX_SHORT_TEXT_CHARS,
        )

    @field_validator("actions")
    @classmethod
    def bound_actions(cls, actions: list[ActionProposal]) -> list[ActionProposal]:
        if len(actions) > 8:
            raise ValueError("actions exceeds 8 items")
        return actions


class SignalAssessment(StrictModel):
    summary: str = Field(min_length=1, max_length=MAX_LONG_TEXT_CHARS)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)
    confidence: StrictFloat = Field(ge=0, le=1)
    uncertainty_reason: str | None = Field(default=None, max_length=MAX_LONG_TEXT_CHARS)

    @field_validator("evidence_refs")
    @classmethod
    def bound_evidence_refs(cls, values: list[str]) -> list[str]:
        return _validate_text_list(
            values,
            field_name="evidence_refs",
            max_items=8,
            max_chars=MAX_IDENTIFIER_CHARS,
        )


class CapabilityAssessment(StrictModel):
    available: list[str] = Field(max_length=8)
    inaccessible: list[str] = Field(max_length=8)
    missing: list[str] = Field(max_length=8)
    notes: list[str] = Field(max_length=12)

    @field_validator("available", "inaccessible", "missing", "notes")
    @classmethod
    def bound_assessment_lists(
        cls, values: list[str], info: ValidationInfo
    ) -> list[str]:
        max_chars = (
            MAX_SHORT_TEXT_CHARS if info.field_name == "notes" else MAX_IDENTIFIER_CHARS
        )
        max_items = 12 if info.field_name == "notes" else 8
        return _validate_text_list(
            values,
            field_name=info.field_name,
            max_items=max_items,
            max_chars=max_chars,
        )


class PlannerAssessment(StrictModel):
    candidate: CandidateProposal | None = None
    case_plan: CasePlanProposal | None = None

    @model_validator(mode="after")
    def require_exactly_one_output(self) -> "PlannerAssessment":
        if (self.candidate is None) == (self.case_plan is None):
            raise ValueError("exactly one of candidate or case_plan is required")
        return self


class OrchestrationResult(StrictModel):
    status: OrchestrationStatus
    output: CandidateProposal | CasePlanProposal | None
    error_code: Literal["AGENT_OUTPUT_INVALID"] | None = None
    output_attempts: int = Field(ge=0, le=2)
    committed: bool
    tool_calls: list[str]
    tool_registry: dict[str, list[str]]
    external_mutation_count: Literal[0] = 0
