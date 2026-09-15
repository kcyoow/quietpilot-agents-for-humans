"""Typed, fail-closed boundary for the AgentCore Runtime entrypoint."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bedrock_model import BedrockModelFactory
from .context import InMemoryContextRepository
from .discovery import discover_action_ready_candidates
from .google_connector import GoogleAuthorizationRequired, GoogleConnector
from .local_model import AgentModelFactory
from .local_preparation import LocalPreparationResult
from .mail_interests import (
    MailInterestProfile,
    MailTitle,
    match_interest_mail,
    recommend_mail_tags,
)
from .models import (
    ActionProposal,
    CapabilityRecord,
    CapabilityStatus,
    CaseType,
    EvidenceRecord,
    OrchestrationRequest,
    ProposalStage,
    ResolvedGmailCaseEvidence,
    Risk,
)
from .runtime import ProposalOrchestrator


class AgentCoreContext(Protocol):
    session_id: str | None
    request_headers: Mapping[str, str] | None
    request: object | None


class GoogleConnectorInvocation(BaseModel):
    """Trusted Lambda-to-Runtime commands for the real Google connector."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal[
        "GOOGLE_AUTHORIZE",
        "GOOGLE_SCAN",
        "GOOGLE_SCAN_PAGE",
        "GOOGLE_HISTORY_SYNC",
        "GOOGLE_RENEW_WATCH",
        "GOOGLE_HISTORY_HEAD",
        "GOOGLE_DISCONNECT",
        "GOOGLE_MAIL_SETUP",
        "GOOGLE_INTEREST_TAGS",
        "GOOGLE_INTEREST_SCAN",
        "GOOGLE_INTEREST_SCAN_PAGE",
        "GOOGLE_INTEREST_HISTORY_SYNC",
    ]
    user_id: str = Field(min_length=1, max_length=128)
    callback_url: str | None = Field(default=None, max_length=2048)
    state: str | None = Field(default=None, min_length=32, max_length=256)
    start_history_id: str | None = Field(default=None, pattern=r"^[0-9]{1,64}$")
    page_token: str | None = Field(default=None, min_length=1, max_length=2048)
    interest_profile: MailInterestProfile | None = None

    @model_validator(mode="after")
    def require_authorization_fields(self) -> GoogleConnectorInvocation:
        interest_operations = {
            "GOOGLE_INTEREST_SCAN",
            "GOOGLE_INTEREST_SCAN_PAGE",
            "GOOGLE_INTEREST_HISTORY_SYNC",
        }
        if self.operation in interest_operations:
            if self.interest_profile is None or not self.interest_profile.configured:
                raise ValueError("interest scan requires a configured interest_profile")
        elif self.interest_profile is not None:
            raise ValueError("interest_profile is only valid for interest scans")
        if self.operation == "GOOGLE_AUTHORIZE":
            if not self.callback_url or not self.callback_url.startswith("https://"):
                raise ValueError("GOOGLE_AUTHORIZE requires an HTTPS callback_url")
            if self.state is None:
                raise ValueError("GOOGLE_AUTHORIZE requires state")
        elif self.operation in {"GOOGLE_HISTORY_SYNC", "GOOGLE_INTEREST_HISTORY_SYNC"}:
            if self.start_history_id is None:
                raise ValueError("GOOGLE_HISTORY_SYNC requires start_history_id")
            if (
                self.callback_url is not None
                or self.state is not None
                or self.page_token is not None
            ):
                raise ValueError("history sync fields do not match GOOGLE_HISTORY_SYNC")
        elif self.operation in {"GOOGLE_SCAN_PAGE", "GOOGLE_INTEREST_SCAN_PAGE"}:
            if self.page_token is None or any(
                character.isspace() for character in self.page_token
            ):
                raise ValueError("GOOGLE_SCAN_PAGE requires a safe page_token")
            if (
                self.callback_url is not None
                or self.state is not None
                or self.start_history_id is not None
            ):
                raise ValueError("scan-page fields do not match GOOGLE_SCAN_PAGE")
        elif (
            self.callback_url is not None
            or self.state is not None
            or self.start_history_id is not None
            or self.page_token is not None
        ):
            raise ValueError(
                "connector operation fields do not match the selected operation"
            )
        if (
            self.operation
            not in {"GOOGLE_HISTORY_SYNC", "GOOGLE_INTEREST_HISTORY_SYNC"}
            and self.start_history_id is not None
        ):
            raise ValueError("start_history_id is only valid for GOOGLE_HISTORY_SYNC")
        if (
            self.operation not in {"GOOGLE_SCAN_PAGE", "GOOGLE_INTEREST_SCAN_PAGE"}
            and self.page_token is not None
        ):
            raise ValueError("page_token is only valid for GOOGLE_SCAN_PAGE")
        return self


class AgentCoreInvocation(BaseModel):
    """Trusted Lambda-to-Runtime envelope; every record remains owner-bound."""

    model_config = ConfigDict(extra="forbid")

    request: OrchestrationRequest
    evidence: list[EvidenceRecord] = Field(min_length=1, max_length=8)
    capabilities: list[CapabilityRecord] = Field(default_factory=list, max_length=8)
    google_account_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    previous_local_preparation: LocalPreparationResult | None = None

    @model_validator(mode="after")
    def require_exact_owned_context(self) -> AgentCoreInvocation:
        user_id = self.request.user_id
        if any(record.user_id != user_id for record in self.evidence):
            raise ValueError("evidence owner does not match request owner")
        if any(record.user_id != user_id for record in self.capabilities):
            raise ValueError("capability owner does not match request owner")

        evidence_refs = [record.ref for record in self.evidence]
        if len(evidence_refs) != len(set(evidence_refs)):
            raise ValueError("evidence references must be unique")
        if set(evidence_refs) != set(self.request.evidence_refs):
            raise ValueError("evidence records must exactly match requested references")

        capability_ids = [record.capability_id for record in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("capability IDs must be unique")
        if set(capability_ids) != set(self.request.capability_ids):
            raise ValueError("capability records must exactly match requested IDs")
        previous = self.previous_local_preparation
        if previous is not None and (
            self.request.proposal_stage is not ProposalStage.CASE_PLANNING
            or len(self.request.requested_actions) != 1
            or self.request.requested_actions[0].connector != "quietpilot"
            or self.request.requested_actions[0].verb
            not in {"prepare_reply", "prepare_task", "prepare_reminder"}
            or self.request.requested_actions[0].parameters.get("source_ref")
            != previous.source_ref
            or previous.source_ref not in evidence_refs
            or previous.status not in {"READY", "NO_ACTION"}
        ):
            raise ValueError("Previous preparation does not match this owned Case")
        return self


class CalendarInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal[
        "GOOGLE_CALENDAR_AUTHORIZE", "GOOGLE_CALENDAR_STATUS", "GOOGLE_CALENDAR_EXECUTE"
    ]
    user_id: str = Field(min_length=1, max_length=128)
    callback_url: str | None = Field(default=None, max_length=2048)
    state: str | None = Field(default=None, min_length=32, max_length=256)
    operation_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9._:-]{8,128}$")
    account_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    parameters: dict[str, object] | None = None

    @model_validator(mode="after")
    def exact_fields(self) -> CalendarInvocation:
        if self.operation == "GOOGLE_CALENDAR_AUTHORIZE":
            if (
                not self.callback_url
                or not self.callback_url.startswith("https://")
                or self.state is None
            ):
                raise ValueError("Calendar authorization requires callback and state")
        elif self.callback_url is not None or self.state is not None:
            raise ValueError("Calendar authorization fields do not match operation")
        if self.operation == "GOOGLE_CALENDAR_EXECUTE":
            from .google_calendar import normalize_calendar_parameters

            if (
                self.operation_id is None
                or self.account_hash is None
                or self.parameters is None
            ):
                raise ValueError("Calendar execution envelope is incomplete")
            self.parameters = normalize_calendar_parameters(self.parameters)
        elif any(
            value is not None
            for value in (self.operation_id, self.account_hash, self.parameters)
        ):
            raise ValueError("Calendar execution fields do not match operation")
        return self


def run_agentcore_invocation(
    payload: object,
    context: AgentCoreContext,
    *,
    model_factory: AgentModelFactory | None = None,
    google_connector: GoogleConnector | None = None,
    workload_access_token: str | None = None,
) -> dict[str, object]:
    """Validate the trusted IAM caller's payload before entering the graph."""

    if isinstance(payload, Mapping) and "operation" in payload:
        if payload.get("operation") in {
            "GOOGLE_CALENDAR_AUTHORIZE",
            "GOOGLE_CALENDAR_STATUS",
            "GOOGLE_CALENDAR_EXECUTE",
        }:
            calendar = CalendarInvocation.model_validate(payload)
            token = workload_access_token or _runtime_workload_access_token()
            connector = google_connector or GoogleConnector.from_environment()
            if calendar.operation == "GOOGLE_CALENDAR_AUTHORIZE":
                return connector.authorize(
                    workload_access_token=token,
                    callback_url=calendar.callback_url or "",
                    state=calendar.state or "",
                    calendar=True,
                )
            if calendar.operation == "GOOGLE_CALENDAR_STATUS":
                return connector.calendar_status(workload_access_token=token)
            try:
                return connector.execute_calendar(
                    workload_access_token=token,
                    operation_id=calendar.operation_id or "",
                    account_hash=calendar.account_hash or "",
                    parameters=calendar.parameters or {},
                )
            except GoogleAuthorizationRequired:
                return {
                    "status": "FAILED",
                    "verified": False,
                    "error_code": "GOOGLE_AUTH_REQUIRED",
                    "result_ref": None,
                    "html_url": None,
                }
        invocation = GoogleConnectorInvocation.model_validate(payload)
        token = workload_access_token or _runtime_workload_access_token()
        connector = google_connector or GoogleConnector.from_environment()
        if invocation.operation == "GOOGLE_MAIL_SETUP":
            return connector.setup_mail(workload_access_token=token)
        if invocation.operation == "GOOGLE_INTEREST_TAGS":
            sample = connector.interest_titles(workload_access_token=token)
            titles = [MailTitle.model_validate(item) for item in sample["titles"]]
            tags = recommend_mail_tags(
                titles, model_factory or BedrockModelFactory.from_environment()
            )
            return {
                "status": "INTEREST_TAGS",
                "tags": [item.model_dump(mode="json") for item in tags],
                "title_count": len(titles),
                "sampled": sample["sampled"],
                "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        if invocation.operation in {
            "GOOGLE_INTEREST_SCAN",
            "GOOGLE_INTEREST_SCAN_PAGE",
            "GOOGLE_INTEREST_HISTORY_SYNC",
        }:
            if invocation.operation == "GOOGLE_INTEREST_SCAN":
                source = connector.scan_and_watch(workload_access_token=token)
            elif invocation.operation == "GOOGLE_INTEREST_SCAN_PAGE":
                source = connector.scan_page(
                    workload_access_token=token,
                    page_token=invocation.page_token or "",
                )
            else:
                source = connector.sync_history(
                    workload_access_token=token,
                    start_history_id=invocation.start_history_id or "",
                )
            assert invocation.interest_profile is not None
            return _google_interest_response(
                user_id=invocation.user_id,
                profile=invocation.interest_profile,
                source=source,
                model_factory=model_factory or BedrockModelFactory.from_environment(),
            )
        if invocation.operation == "GOOGLE_AUTHORIZE":
            return connector.authorize(
                workload_access_token=token,
                callback_url=invocation.callback_url or "",
                state=invocation.state or "",
            )
        if invocation.operation == "GOOGLE_SCAN":
            return _scan_google_and_watch(
                invocation=invocation,
                connector=connector,
                workload_access_token=token,
                model_factory=model_factory,
            )
        if invocation.operation == "GOOGLE_SCAN_PAGE":
            return _scan_google_page(
                invocation=invocation,
                connector=connector,
                workload_access_token=token,
                model_factory=model_factory,
            )
        if invocation.operation == "GOOGLE_HISTORY_SYNC":
            return _sync_google_history(
                invocation=invocation,
                connector=connector,
                workload_access_token=token,
                model_factory=model_factory,
            )
        if invocation.operation == "GOOGLE_RENEW_WATCH":
            return connector.renew_watch(workload_access_token=token)
        if invocation.operation == "GOOGLE_HISTORY_HEAD":
            return connector.history_head(workload_access_token=token)
        return connector.disconnect(workload_access_token=token)

    invocation = AgentCoreInvocation.model_validate(payload)
    # AgentCore's RequestContext deliberately does not expose runtimeUserId.
    # The invoking Lambda must bind runtimeUserId and request.user_id to the same
    # verified Cognito subject through InvokeAgentRuntimeForUser. This boundary
    # still enforces exact ownership across every record in the signed payload.
    del context

    if invocation.request.proposal_stage is ProposalStage.CASE_PLANNING and (
        invocation.google_account_hash is not None
        or "google.calendar.events.create" in invocation.request.capability_ids
    ):
        gmail = [record for record in invocation.evidence if record.source == "gmail"]
        if gmail:
            if invocation.google_account_hash is None:
                return {
                    "status": "EXCEPTION_REQUIRED",
                    "committed": False,
                    "external_mutation_count": 0,
                    "error_code": "GOOGLE_CASE_SOURCE_UNAVAILABLE",
                }
            from .google_connector import GoogleCaseEvidenceUnavailable

            token = workload_access_token or _runtime_workload_access_token()
            connector = google_connector or GoogleConnector.from_environment()
            try:
                current = connector.resolve_case_evidence(
                    workload_access_token=token,
                    account_hash=invocation.google_account_hash,
                    evidence=[record.model_dump(mode="json") for record in gmail],
                )
            except (GoogleCaseEvidenceUnavailable, GoogleAuthorizationRequired):
                return {
                    "status": "EXCEPTION_REQUIRED",
                    "committed": False,
                    "external_mutation_count": 0,
                    "error_code": "GOOGLE_CASE_SOURCE_UNAVAILABLE",
                }
            refreshed = {
                record["ref"]: ResolvedGmailCaseEvidence.model_validate(record)
                for record in current
            }
            if set(refreshed) != {record.ref for record in gmail} or any(
                record.user_id != invocation.request.user_id
                for record in refreshed.values()
            ):
                raise ValueError(
                    "Resolved Calendar sources do not match the owned Case"
                )
            invocation = invocation.model_copy(
                update={
                    "evidence": [
                        refreshed.get(record.ref, record)
                        for record in invocation.evidence
                    ]
                }
            )

    repository = InMemoryContextRepository(
        evidence=invocation.evidence,
        capabilities=invocation.capabilities,
    )
    factory = model_factory or BedrockModelFactory.from_environment()
    from .calendar_planner import CalendarDraftOutputError
    from .context import ContextAccessDenied
    from .local_preparation import (
        LocalPreparationOutputError,
        LocalPreparationSourceUnavailable,
        prepare_local_artifact,
    )

    local = None
    try:
        request, calendar_prepared = _calendar_case_request(
            invocation.request, repository, factory
        )
        original = invocation.request
        local_actions = original.requested_actions
        if (
            original.proposal_stage is ProposalStage.CASE_PLANNING
            and len(local_actions) == 1
            and local_actions[0].connector == "quietpilot"
            and local_actions[0].verb
            in {"prepare_reply", "prepare_task", "prepare_reminder"}
            and (
                not request.requested_actions
                or request.requested_actions[0].connector == "quietpilot"
            )
        ):
            local = prepare_local_artifact(
                original,
                repository.open_scope(original),
                factory,
                previous=invocation.previous_local_preparation,
            )
    except LocalPreparationSourceUnavailable:
        print(
            json.dumps(
                {
                    "event": "case_preparation_rejected",
                    "stage": "local_source",
                    "missing_body_count": sum(
                        not record.untrusted_text or not record.untrusted_text.strip()
                        for record in invocation.evidence
                    ),
                    "snippet_only_count": sum(
                        "source_content=snippet_only" in record.facts
                        for record in invocation.evidence
                    ),
                    "truncated_count": sum(
                        "source_truncated=true" in record.facts
                        for record in invocation.evidence
                    ),
                    "google_account_bound": invocation.google_account_hash is not None,
                },
                separators=(",", ":"),
            )
        )
        return {
            "status": "EXCEPTION_REQUIRED",
            "committed": False,
            "external_mutation_count": 0,
            "error_code": "CASE_PREPARATION_FAILED",
        }
    except LocalPreparationOutputError:
        return {
            "status": "EXCEPTION_REQUIRED",
            "committed": False,
            "external_mutation_count": 0,
            "error_code": "CASE_PREPARATION_FAILED",
        }
    except (CalendarDraftOutputError, ContextAccessDenied):
        return {
            "status": "EXCEPTION_REQUIRED",
            "committed": False,
            "external_mutation_count": 0,
            "error_code": "CALENDAR_PREPARATION_FAILED",
        }
    if (
        "google.calendar.events.create" in request.capability_ids
        and not request.requested_actions
        and local is None
    ):
        return {
            "status": "EXCEPTION_REQUIRED",
            "committed": False,
            "external_mutation_count": 0,
            "error_code": "CALENDAR_DETAILS_REQUIRED",
        }
    if local is not None:
        try:
            return _commit_local_preparation(invocation.request, repository, local)
        except (ContextAccessDenied, TypeError, ValueError):
            print(
                json.dumps(
                    {
                        "event": "case_preparation_rejected",
                        "stage": "local_commit",
                        "local_status": local.status,
                    },
                    separators=(",", ":"),
                )
            )
            return {
                "status": "EXCEPTION_REQUIRED",
                "committed": False,
                "external_mutation_count": 0,
                "error_code": "CASE_PREPARATION_FAILED",
            }
    if calendar_prepared:
        try:
            return _commit_calendar_preparation(request, repository)
        except (ContextAccessDenied, TypeError, ValueError):
            print('{"event":"case_preparation_rejected","stage":"calendar_commit"}')
            return {
                "status": "EXCEPTION_REQUIRED",
                "committed": False,
                "external_mutation_count": 0,
                "error_code": "CALENDAR_PREPARATION_FAILED",
            }
    return (
        ProposalOrchestrator(repository, factory).run(request).model_dump(mode="json")
    )


def _commit_local_preparation(
    original: OrchestrationRequest,
    repository: InMemoryContextRepository,
    local: LocalPreparationResult,
) -> dict[str, object]:
    """Publish this invocation's verified local result through the existing contract."""
    from collections import Counter

    from .context import ContextAccessDenied, ProposalTransaction, canonical_action_key
    from .models import CasePlanProposal, OrchestrationResult, OrchestrationStatus
    from .proposal_contract import (
        _action_is_available,
        _trusted_actions,
        _validate_grounding,
    )

    kinds = {
        "prepare_reply": "REPLY_DRAFT",
        "prepare_task": "CHECKLIST",
        "prepare_reminder": "REMINDER",
    }
    if (
        original.proposal_stage is not ProposalStage.CASE_PLANNING
        or len(original.requested_actions) != 1
    ):
        raise ValueError("Local result is outside a selected Case")
    selected = original.requested_actions[0]
    scope = repository.open_scope(original)
    if (
        selected.connector != "quietpilot"
        or selected.verb not in kinds
        or selected.required_scopes
        or not selected.reversible
        or selected.parameters.get("source_ref") != local.source_ref
        or local.source_ref not in original.evidence_refs
        or not _action_is_available(selected, scope)
    ):
        raise ContextAccessDenied("Local result does not match its selected action")
    actions = []
    if local.status == "READY":
        if (
            local.artifact_type != kinds[selected.verb]
            or not local.title.strip()
            or not local.content.strip()
            or local.question
        ):
            raise ValueError("Local artifact is incomplete")
        actions = [
            ActionProposal.model_validate(
                {
                    **selected.model_dump(mode="json"),
                    "parameters": {
                        key: getattr(local, key)
                        for key in ("source_ref", "title", "content", "artifact_type")
                    },
                }
            )
        ]
    elif local.artifact_type != "NONE" or local.content:
        raise ValueError("Actionless preparation returned an artifact")
    elif local.status == "NO_ACTION":
        if local.title or local.question:
            raise ValueError("No-action preparation is incomplete")
    elif (
        not local.question.strip().endswith(("?", "？"))
        or local.question.count("?") + local.question.count("？") != 1
        or "\n" in local.question
    ):
        raise ValueError("Local preparation needs one specific question")
    request = OrchestrationRequest.model_validate(
        {
            **original.model_dump(mode="json"),
            "requested_actions": [action.model_dump(mode="json") for action in actions],
        }
    )
    if _trusted_actions(request, scope) != actions:
        raise ContextAccessDenied("Prepared action is outside the current capability")
    output = CasePlanProposal(
        case_type=original.case_type,
        goal=original.goal,
        explanation=local.explanation,
        evidence_revisions=scope.evidence_revisions(),
        preparation_steps=[],
        actions=actions,
        decision_question=local.question or local.explanation,
    )
    _validate_grounding(output, request, scope)
    transaction = ProposalTransaction(
        allowed_evidence_refs=scope.allowed_evidence_refs,
        allowed_capability_ids=scope.allowed_capability_ids,
        expected_goal=original.goal,
        expected_case_type=original.case_type,
        expected_evidence_revisions=scope.evidence_revisions(),
        expected_fingerprint_inputs=[*original.evidence_refs, original.goal],
        minimum_risk=original.risk,
        allowed_action_counts=dict(
            Counter(canonical_action_key(action) for action in actions)
        ),
    )
    transaction.stage_case_plan(output.model_dump(mode="json"))
    transaction.commit(output)
    response = OrchestrationResult(
        status=OrchestrationStatus.PROPOSED,
        output=transaction.committed,
        output_attempts=0,
        committed=True,
        tool_calls=[],
        tool_registry={},
        external_mutation_count=0,
    ).model_dump(mode="json")
    response["local_preparation"] = local.model_dump(mode="json")
    return response


def _commit_calendar_preparation(
    request: OrchestrationRequest,
    repository: InMemoryContextRepository,
) -> dict[str, object]:
    """Stage this invocation's verified Calendar draft for the existing approval flow."""
    from .context import ContextAccessDenied, ProposalTransaction, canonical_action_key
    from .google_calendar import normalize_calendar_parameters
    from .google_connector import CALENDAR_EVENTS_SCOPE
    from .models import CasePlanProposal, OrchestrationResult, OrchestrationStatus
    from .proposal_contract import (
        _trusted_actions,
        _validate_grounding,
    )

    if (
        request.proposal_stage is not ProposalStage.CASE_PLANNING
        or len(request.requested_actions) != 1
    ):
        raise ValueError("Calendar preparation requires one selected Case action")
    scope = repository.open_scope(request)
    action = request.requested_actions[0]
    source_ref = action.parameters.get("source_ref")
    if (
        action.connector != "google"
        or action.verb != "calendar_event_create"
        or action.target_resource != "primary"
        or action.required_scopes != [CALENDAR_EVENTS_SCOPE]
        or not action.reversible
        or action.verification_method != "calendar_event_readback"
        or source_ref not in request.evidence_refs
        or action.risk not in {Risk.MEDIUM, Risk.HIGH}
        or _trusted_actions(request, scope) != [action]
    ):
        raise ContextAccessDenied("Calendar preparation changed its verified boundary")
    parameters = {
        key: value for key, value in action.parameters.items() if key != "source_ref"
    }
    if normalize_calendar_parameters(parameters) != parameters:
        raise ValueError("Calendar preparation is not normalized")
    output = CasePlanProposal(
        case_type=request.case_type,
        goal=request.goal,
        explanation="Calendar draft ready from the verified source.",
        evidence_revisions=scope.evidence_revisions(),
        preparation_steps=[],
        actions=[action],
        decision_question="Add this event to your calendar?",
    )
    _validate_grounding(output, request, scope)
    transaction = ProposalTransaction(
        allowed_evidence_refs=scope.allowed_evidence_refs,
        allowed_capability_ids=scope.allowed_capability_ids,
        expected_goal=request.goal,
        expected_case_type=request.case_type,
        expected_evidence_revisions=scope.evidence_revisions(),
        expected_fingerprint_inputs=[*request.evidence_refs, request.goal],
        minimum_risk=request.risk,
        allowed_action_counts={canonical_action_key(action): 1},
    )
    transaction.stage_case_plan(output.model_dump(mode="json"))
    transaction.commit(output)
    return OrchestrationResult(
        status=OrchestrationStatus.PROPOSED,
        output=transaction.committed,
        output_attempts=0,
        committed=True,
        tool_calls=[],
        tool_registry={},
        external_mutation_count=0,
    ).model_dump(mode="json")


def _calendar_case_request(
    request: OrchestrationRequest,
    repository: InMemoryContextRepository,
    factory: AgentModelFactory,
) -> tuple[OrchestrationRequest, bool]:
    """Prepare a real action only from current source and available Calendar access."""
    if (
        request.proposal_stage is not ProposalStage.CASE_PLANNING
        or "google.calendar.events.create" not in request.capability_ids
    ):
        return request, False
    from .calendar_planner import CalendarDraftOutputError, build_calendar_draft
    from .google_connector import CALENDAR_EVENTS_SCOPE

    scope = repository.open_scope(request)
    capability = scope.capabilities["google.calendar.events.create"]
    if (
        capability.status is not CapabilityStatus.AVAILABLE
        or capability.operations != ["google.calendar_event_create"]
        or capability.required_scopes != [CALENDAR_EVENTS_SCOPE]
    ):
        return request, False
    if request.requested_actions and (
        len(request.requested_actions) != 1
        or request.requested_actions[0].connector != "quietpilot"
        or request.requested_actions[0].verb not in {"prepare_reminder", "prepare_task"}
    ):
        return request, False
    # A user correction must itself support the replacement. An incomplete new
    # request must never fall back to executing the superseded original dates.
    direct_refs = [
        ref
        for ref in request.evidence_refs
        if scope.evidence[ref].source in {"direct", "direct_request"}
        and scope.evidence[ref].untrusted_text
    ]
    draft_request = (
        request.model_copy(
            update={
                "case_type": CaseType.DIRECT_DELEGATION,
                "evidence_refs": direct_refs,
            }
        )
        if direct_refs
        else request
    )
    try:
        draft = build_calendar_draft(draft_request, scope, factory)
    except CalendarDraftOutputError:
        if not request.requested_actions:
            raise
        # Calendar promotion is optional for an explicitly requested local
        # reminder/checklist. Its failure grants no authority and must not
        # prevent the independently validated original preparation.
        print('{"event":"case_calendar_fallback_to_local"}')
        return request, False
    if draft is None:
        return request.model_copy(update={"requested_actions": []}), False
    action = ActionProposal(
        connector="google",
        target_resource="primary",
        verb="calendar_event_create",
        parameters=draft,
        risk=Risk.HIGH if request.risk is Risk.HIGH else Risk.MEDIUM,
        reversible=True,
        required_scopes=[CALENDAR_EVENTS_SCOPE],
        verification_method="calendar_event_readback",
    )
    # This transient marker is never accepted from caller payloads. Only the
    # fresh planner+verifier result above can skip redundant plan regeneration.
    return request.model_copy(update={"requested_actions": [action]}), True


def _runtime_workload_access_token() -> str:
    from bedrock_agentcore.runtime.context import BedrockAgentCoreContext

    token = BedrockAgentCoreContext.get_workload_access_token()
    if not token:
        raise RuntimeError("AgentCore workload access token is missing")
    return token


def _sync_google_history(
    *,
    invocation: GoogleConnectorInvocation,
    connector: GoogleConnector,
    workload_access_token: str,
    model_factory: AgentModelFactory | None,
) -> dict[str, object]:
    sync = connector.sync_history(
        workload_access_token=workload_access_token,
        start_history_id=invocation.start_history_id or "",
    )
    return _google_signal_response(
        user_id=invocation.user_id,
        source=sync,
        model_factory=model_factory,
    )


def _scan_google_and_watch(
    *,
    invocation: GoogleConnectorInvocation,
    connector: GoogleConnector,
    workload_access_token: str,
    model_factory: AgentModelFactory | None,
) -> dict[str, object]:
    scan = connector.scan_and_watch(workload_access_token=workload_access_token)
    return _google_signal_response(
        user_id=invocation.user_id,
        source=scan,
        model_factory=model_factory,
    )


def _scan_google_page(
    *,
    invocation: GoogleConnectorInvocation,
    connector: GoogleConnector,
    workload_access_token: str,
    model_factory: AgentModelFactory | None,
) -> dict[str, object]:
    page = connector.scan_page(
        workload_access_token=workload_access_token,
        page_token=invocation.page_token or "",
    )
    return _google_signal_response(
        user_id=invocation.user_id,
        source=page,
        model_factory=model_factory,
    )


def _google_interest_response(
    *,
    user_id: str,
    profile: MailInterestProfile,
    source: Mapping[str, object],
    model_factory: AgentModelFactory,
) -> dict[str, object]:
    raw_evidence = source.get("evidence")
    if not isinstance(raw_evidence, list) or any(
        not isinstance(record, Mapping) for record in raw_evidence
    ):
        raise TypeError("Google interest scan returned invalid evidence")
    evidence = [
        EvidenceRecord.model_validate({**record, "user_id": user_id})
        for record in raw_evidence
    ]
    matches = match_interest_mail(profile, evidence, model_factory)
    records = {record.ref: record for record in evidence}
    matched_refs = {match.evidence_ref for match in matches}
    # Only relevant evidence may reach the existing proposal-only planner. Its
    # Candidate output remains distinct from informational mail results.
    try:
        response = _google_signal_response(
            user_id=user_id,
            source={
                **source,
                "evidence": [
                    record.model_dump(mode="json", exclude={"user_id"})
                    for record in evidence
                    if record.ref in matched_refs
                ],
            },
            model_factory=model_factory,
        )
    except Exception:  # noqa: BLE001 - isolate optional planning from validated mail
        # Matching has already passed its independent grounding checks. A
        # proposal failure must neither hide those results nor publish an
        # unvalidated action. Keep exception details out of the mail response.
        response = {
            **source,
            "candidate": None,
            "candidates": [],
            "discovery_validated": False,
            "unresolved_evidence_count": len(matched_refs),
            "discovery_reason": "MAIL_ACTION_PREPARATION_INCOMPLETE",
        }
    response.update(
        {
            "evidence": [
                {
                    **record.model_dump(mode="json", exclude={"user_id"}),
                    "untrusted_text": None,
                }
                for record in evidence
            ],
            "interest_profile_revision": profile.revision,
            "interest_validated": True,
            "interest_matches": [
                {
                    **match.model_dump(mode="json"),
                    "title": records[match.evidence_ref].title,
                    "sender_domain": _mail_fact(
                        records[match.evidence_ref], "sender_domain"
                    )
                    or "unknown",
                    "received_at": _mail_received_at(records[match.evidence_ref]),
                }
                for match in matches
            ],
        }
    )
    return response


def _mail_fact(record: EvidenceRecord, name: str) -> str | None:
    values = [
        value.removeprefix(f"{name}=")
        for value in record.facts
        if value.startswith(f"{name}=")
    ]
    return values[0] if len(values) == 1 else None


def _mail_received_at(record: EvidenceRecord) -> str | None:
    value = _mail_fact(record, "received_at_unix_ms")
    if value is None or not value.isdigit():
        return None
    try:
        return (
            datetime.fromtimestamp(int(value) / 1000, UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (ValueError, OverflowError, OSError):
        return None


def _google_signal_response(
    *,
    user_id: str,
    source: Mapping[str, object],
    model_factory: AgentModelFactory | None,
) -> dict[str, object]:
    raw_evidence = source.get("evidence")
    if not isinstance(raw_evidence, list):
        raise TypeError("Google signal returned invalid evidence")
    evidence = [
        EvidenceRecord.model_validate({**record, "user_id": user_id})
        for record in raw_evidence
        if isinstance(record, Mapping)
    ]
    if len(evidence) != len(raw_evidence):
        raise TypeError("Google signal returned a malformed evidence record")

    response = dict(source)
    response.update(
        {
            "evidence": [
                {
                    **record.model_dump(mode="json", exclude={"user_id"}),
                    "untrusted_text": None,
                }
                for record in evidence
            ],
            "candidate": None,
            "candidates": [],
            "discovery_validated": True,
            "unresolved_evidence_count": 0,
            "discovery_reason": "No new source to review.",
        }
    )
    if not evidence:
        return response

    evidence_refs = [record.ref for record in evidence]
    goal = "Find useful preparation tasks in new Gmail messages"
    capabilities = _preparation_capabilities(user_id)
    request = OrchestrationRequest(
        user_id=user_id,
        case_type=CaseType.CONNECTED_SIGNAL,
        goal=goal,
        evidence_refs=evidence_refs,
        capability_ids=[record.capability_id for record in capabilities],
        primary_group_hint="action-ready",
        tags=["gmail"],
        risk=Risk.LOW,
        requested_actions=[],
        conflicting_evidence=False,
    )
    repository = InMemoryContextRepository(
        evidence=evidence,
        capabilities=capabilities,
    )
    scope = repository.open_scope(request)
    result = discover_action_ready_candidates(
        request,
        scope,
        model_factory or BedrockModelFactory.from_environment(),
    )
    response["discovery_reason"] = result.reason
    response["discovery_validated"] = result.validated
    response["unresolved_evidence_count"] = result.unresolved_evidence_count
    if (
        not result.validated
        or not result.candidates
        or result.external_mutation_count != 0
    ):
        return response
    candidates = [candidate.model_dump(mode="json") for candidate in result.candidates]
    # Keep the first Candidate during the rolling deployment window for an old
    # Worker while the new Worker consumes the complete list.
    response["candidate"] = candidates[0]
    response["candidates"] = candidates
    return response


def _preparation_capabilities(user_id: str) -> list[CapabilityRecord]:
    """Capabilities implemented by the proposal/Case preparation boundary itself."""

    return [
        CapabilityRecord(
            user_id=user_id,
            capability_id="quietpilot.reminder.prepare",
            connector="quietpilot",
            status=CapabilityStatus.AVAILABLE,
            operations=["quietpilot.prepare_reminder"],
            required_scopes=[],
        ),
        CapabilityRecord(
            user_id=user_id,
            capability_id="quietpilot.task.prepare",
            connector="quietpilot",
            status=CapabilityStatus.AVAILABLE,
            operations=["quietpilot.prepare_task"],
            required_scopes=[],
        ),
        CapabilityRecord(
            user_id=user_id,
            capability_id="quietpilot.reply.prepare",
            connector="quietpilot",
            status=CapabilityStatus.AVAILABLE,
            operations=["quietpilot.prepare_reply"],
            required_scopes=[],
        ),
    ]
