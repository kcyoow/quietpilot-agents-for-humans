"""Invocation-scoped context and proposal staging boundaries."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from strands import tool

from .models import (
    ActionProposal,
    CandidateProposal,
    CapabilityRecord,
    CasePlanProposal,
    CaseType,
    EvidenceRecord,
    OrchestrationRequest,
    Risk,
    risk_at_least,
)

MAX_CONTEXT_ITEMS = 8
MAX_INVOCATION_CONTEXT_BYTES = 32_768
EXPECTED_SPECIALIST_SEQUENCE = (
    "signal_analyst",
    "capability_analyst",
    "case_planner",
)


class ContextAccessDenied(Exception):
    """Generic denial that does not reveal whether another user's record exists."""


class SpecialistPreconditionError(RuntimeError):
    """Raised when a proposal bypasses or corrupts specialist analysis."""


@dataclass
class InvocationAudit:
    expected_specialists: tuple[str, ...] = EXPECTED_SPECIALIST_SEQUENCE
    tool_calls: list[str] = field(default_factory=list)
    specialist_successes: list[str] = field(default_factory=list)
    specialist_fallbacks: set[str] = field(default_factory=set)
    specialist_outputs: dict[str, object] = field(default_factory=dict)
    specialist_error: str | None = None
    context_read_counts: dict[str, int] = field(default_factory=dict)
    context_read_attempts: dict[str, int] = field(default_factory=dict)
    external_mutation_count: int = 0

    def record(self, tool_name: str) -> None:
        self.tool_calls.append(tool_name)

    def record_specialist(self, tool_name: str, output: object) -> None:
        expected_index = len(self.specialist_successes)
        if expected_index >= len(self.expected_specialists):
            self.specialist_error = "too many specialist calls"
            return
        expected = self.expected_specialists[expected_index]
        if tool_name != expected:
            self.specialist_error = (
                f"specialist order violation: expected {expected}, got {tool_name}"
            )
            return
        self.specialist_successes.append(tool_name)
        self.specialist_outputs[tool_name] = output

    def record_specialist_fallback(self, tool_name: str, output: object) -> None:
        self.record_specialist(tool_name, output)
        if self.specialist_error is None:
            self.specialist_fallbacks.add(tool_name)

    def fail_specialist(self, message: str) -> None:
        self.specialist_error = message

    def require_specialists(self) -> None:
        if self.specialist_error is not None:
            print("quietpilot_specialist_precondition_rejected reason=specialist_error")
            raise SpecialistPreconditionError(self.specialist_error)
        if tuple(self.specialist_successes) != self.expected_specialists:
            print("quietpilot_specialist_precondition_rejected reason=sequence")
            raise SpecialistPreconditionError(
                "required specialists must succeed exactly once and in order"
            )
        required_reads: dict[str, int] = {}
        if "signal_analyst" in self.expected_specialists:
            required_reads["read_evidence_context"] = 1
        if "capability_analyst" in self.expected_specialists:
            required_reads["read_capability_context"] = 1
        if self.context_read_counts != required_reads:
            print("quietpilot_specialist_precondition_rejected reason=context_reads")
            raise SpecialistPreconditionError(
                "signal and capability specialists must each complete one bounded read"
            )

    def reserve_context_read(self, tool_name: str) -> None:
        self.context_read_attempts[tool_name] = (
            self.context_read_attempts.get(tool_name, 0) + 1
        )
        count = self.context_read_counts.get(tool_name, 0)
        if count >= 1:
            raise ContextAccessDenied("context read budget exhausted")
        self.context_read_counts[tool_name] = count + 1


@dataclass(frozen=True)
class BoundedContext:
    user_id: str
    evidence: dict[str, EvidenceRecord]
    capabilities: dict[str, CapabilityRecord]
    allowed_evidence_refs: frozenset[str]
    allowed_capability_ids: frozenset[str]

    def read_evidence(self, refs: list[str]) -> list[dict[str, object]]:
        if len(refs) > MAX_CONTEXT_ITEMS or not set(refs).issubset(
            self.allowed_evidence_refs
        ):
            raise ContextAccessDenied("requested evidence is outside this invocation")

        records: list[dict[str, object]] = []
        for ref in refs:
            record = self.evidence.get(ref)
            if record is None or record.user_id != self.user_id:
                raise ContextAccessDenied("requested evidence is unavailable")
            records.append(
                record.model_dump(
                    mode="json",
                    exclude={"user_id"},
                )
            )
        _enforce_byte_budget(records)
        return records

    def read_capabilities(self, capability_ids: list[str]) -> list[dict[str, object]]:
        if len(capability_ids) > MAX_CONTEXT_ITEMS or not set(capability_ids).issubset(
            self.allowed_capability_ids
        ):
            raise ContextAccessDenied("requested capability is outside this invocation")

        records: list[dict[str, object]] = []
        for capability_id in capability_ids:
            record = self.capabilities.get(capability_id)
            if record is None or record.user_id != self.user_id:
                raise ContextAccessDenied("requested capability is unavailable")
            records.append(record.model_dump(mode="json", exclude={"user_id"}))
        _enforce_byte_budget(records)
        return records

    def evidence_revisions(self) -> dict[str, int]:
        return {
            ref: self.evidence[ref].revision
            for ref in sorted(self.allowed_evidence_refs)
            if ref in self.evidence and self.evidence[ref].user_id == self.user_id
        }


class InMemoryContextRepository:
    """Local test repository with the same owner checks expected from DynamoDB."""

    def __init__(
        self,
        *,
        evidence: list[EvidenceRecord],
        capabilities: list[CapabilityRecord],
    ) -> None:
        self._evidence = {(record.user_id, record.ref): record for record in evidence}
        self._capabilities = {
            (record.user_id, record.capability_id): record for record in capabilities
        }

    def open_scope(self, request: OrchestrationRequest) -> BoundedContext:
        if len(request.evidence_refs) > MAX_CONTEXT_ITEMS:
            raise ContextAccessDenied("too many evidence references")
        if len(request.capability_ids) > MAX_CONTEXT_ITEMS:
            raise ContextAccessDenied("too many capability references")

        for ref in request.evidence_refs:
            record = self._evidence.get((request.user_id, ref))
            if record is None:
                raise ContextAccessDenied("requested evidence is unavailable")
        for capability_id in request.capability_ids:
            record = self._capabilities.get((request.user_id, capability_id))
            if record is None:
                raise ContextAccessDenied("requested capability is unavailable")

        scope = BoundedContext(
            user_id=request.user_id,
            evidence={
                ref: self._evidence[(request.user_id, ref)]
                for ref in request.evidence_refs
            },
            capabilities={
                capability_id: self._capabilities[(request.user_id, capability_id)]
                for capability_id in request.capability_ids
            },
            allowed_evidence_refs=frozenset(request.evidence_refs),
            allowed_capability_ids=frozenset(request.capability_ids),
        )
        _enforce_byte_budget(
            {
                "evidence": scope.read_evidence(request.evidence_refs),
                "capabilities": scope.read_capabilities(request.capability_ids),
            }
        )
        return scope


@dataclass
class ProposalTransaction:
    allowed_evidence_refs: frozenset[str]
    allowed_capability_ids: frozenset[str]
    expected_goal: str
    expected_case_type: CaseType
    expected_evidence_revisions: dict[str, int]
    expected_fingerprint_inputs: list[str]
    minimum_risk: Risk
    allowed_action_counts: dict[str, int]
    staged: CandidateProposal | CasePlanProposal | None = None
    committed: CandidateProposal | CasePlanProposal | None = None

    def stage_candidate(self, payload: dict[str, object]) -> dict[str, object]:
        try:
            proposal = CandidateProposal.model_validate(payload)
        except ValueError:
            print("quietpilot_candidate_stage_rejected reason=schema")
            raise
        if not set(proposal.evidence_refs).issubset(self.allowed_evidence_refs):
            print("quietpilot_candidate_stage_rejected reason=evidence_scope")
            raise ContextAccessDenied(
                "candidate cites evidence outside this invocation"
            )
        if not set(proposal.required_capabilities).issubset(
            self.allowed_capability_ids
        ):
            print("quietpilot_candidate_stage_rejected reason=capability_scope")
            raise ContextAccessDenied(
                "candidate cites capability outside this invocation"
            )
        if proposal.outcome != self.expected_goal:
            print("quietpilot_candidate_stage_rejected reason=outcome_changed")
            raise ContextAccessDenied("candidate outcome changed the trusted goal")
        if proposal.fingerprint_inputs != self.expected_fingerprint_inputs:
            print("quietpilot_candidate_stage_rejected reason=fingerprint_changed")
            raise ContextAccessDenied(
                "candidate changed the trusted fingerprint inputs"
            )
        if not risk_at_least(proposal.risk, self.minimum_risk):
            print("quietpilot_candidate_stage_rejected reason=risk_floor")
            raise ContextAccessDenied("candidate risk is below the trusted risk floor")
        self.staged = proposal
        return proposal.model_dump(mode="json")

    def stage_case_plan(self, payload: dict[str, object]) -> dict[str, object]:
        proposal = CasePlanProposal.model_validate(payload)
        if not set(proposal.evidence_revisions).issubset(self.allowed_evidence_refs):
            raise ContextAccessDenied(
                "case plan cites evidence outside this invocation"
            )
        if proposal.goal != self.expected_goal:
            raise ContextAccessDenied("case plan changed the trusted goal")
        if proposal.case_type != self.expected_case_type:
            raise ContextAccessDenied("case plan changed the trusted case type")
        if proposal.evidence_revisions != self.expected_evidence_revisions:
            raise ContextAccessDenied("case plan changed trusted evidence revisions")
        proposed_counts = Counter(
            canonical_action_key(action) for action in proposal.actions
        )
        if any(
            count > self.allowed_action_counts.get(action_key, 0)
            for action_key, count in proposed_counts.items()
        ):
            raise ContextAccessDenied("case plan changed a trusted action envelope")
        self.staged = proposal
        return proposal.model_dump(mode="json")

    def commit(self, output: CandidateProposal | CasePlanProposal) -> None:
        if self.staged is None or self.staged != output:
            raise ValueError("final output does not match the staged proposal")
        self.committed = output

    def discard(self) -> None:
        self.staged = None
        self.committed = None


def build_context_tools(scope: BoundedContext, audit: InvocationAudit) -> list[object]:
    @tool(
        name="read_evidence_context",
        description="Read only the redacted evidence references bound to this invocation.",
    )
    def read_evidence_context(refs: list[str]) -> list[dict[str, object]]:
        audit.record("read_evidence_context")
        audit.reserve_context_read("read_evidence_context")
        return scope.read_evidence(refs)

    @tool(
        name="read_capability_context",
        description="Read only the capability IDs bound to this invocation.",
    )
    def read_capability_context(
        capability_ids: list[str],
    ) -> list[dict[str, object]]:
        audit.record("read_capability_context")
        audit.reserve_context_read("read_capability_context")
        return scope.read_capabilities(capability_ids)

    return [read_evidence_context, read_capability_context]


def build_proposal_tools(
    transaction: ProposalTransaction,
    audit: InvocationAudit,
) -> list[object]:
    @tool(
        name="propose_candidate",
        description="Stage a validated candidate proposal; this performs no external action.",
    )
    def propose_candidate(payload: dict[str, object]) -> dict[str, object]:
        audit.record("propose_candidate")
        audit.require_specialists()
        return transaction.stage_candidate(payload)

    @tool(
        name="propose_case_plan",
        description="Stage a validated case plan; this performs no external action.",
    )
    def propose_case_plan(payload: dict[str, object]) -> dict[str, object]:
        audit.record("propose_case_plan")
        audit.require_specialists()
        return transaction.stage_case_plan(payload)

    return [propose_candidate, propose_case_plan]


def canonical_action_key(action: ActionProposal) -> str:
    return json.dumps(
        action.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _enforce_byte_budget(value: object) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    if len(encoded) > MAX_INVOCATION_CONTEXT_BYTES:
        raise ContextAccessDenied("invocation context exceeds the byte budget")
