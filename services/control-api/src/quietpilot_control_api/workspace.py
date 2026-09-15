"""Owner-bound Case preparation and read model for the mobile control room."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .workspace_records import (
    ACTIVE_STATUSES,
    CASE_TYPES,
    HISTORY_STATUSES,
    RISKS,
    _candidate_key,
    _case_detail_from_items,
    _case_evidence_item,
    _case_item,
    _case_key,
    _case_sort_key,
    _case_summary_from_item,
    _integer,
    _string,
    _string_list,
    _timeline_item,
)

MAX_CASES = 100
MAX_SELECTED_CANDIDATES = 8
MAX_CASE_EVIDENCE = 8
MAX_CASE_ACTIONS = 8
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class WorkspaceInputError(ValueError):
    pass


class WorkspaceNotFound(LookupError):
    pass


class WorkspaceConflict(RuntimeError):
    pass


class WorkspaceNotReady(RuntimeError):
    pass


class CasePreparationQueue(Protocol):
    def send(
        self,
        *,
        user_id: str,
        case_id: str,
        connector: str,
        plan_version: int,
    ) -> None: ...


class WorkspaceStore(Protocol):
    def list_cases(self, user_id: str, bucket: str) -> list[dict[str, object]]: ...

    def get_case(self, user_id: str, case_id: str) -> dict[str, object] | None: ...

    def create_direct_case(
        self,
        user_id: str,
        *,
        case_id: str,
        prompt: str,
        evidence_ref: str,
        now: str,
    ) -> tuple[dict[str, object], bool]: ...

    def convert_candidates(
        self,
        user_id: str,
        *,
        case_id: str,
        candidate_ids: list[str],
        expected_versions: list[int],
        now: str,
    ) -> tuple[dict[str, object], bool]: ...

    def add_message(
        self,
        user_id: str,
        *,
        case_id: str,
        text: str,
        expected_version: int,
        message_id: str,
        evidence_ref: str,
        now: str,
    ) -> tuple[dict[str, object], int]: ...

    def transition_case(
        self,
        user_id: str,
        *,
        case_id: str,
        expected_version: int,
        status: str,
        label: str,
        body: str,
        now: str,
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class WorkspaceService:
    store: WorkspaceStore
    queue: CasePreparationQueue
    execution: Any = None

    def list_cases(self, user_id: str, bucket: object) -> dict[str, object]:
        if bucket not in {"active", "history"}:
            raise WorkspaceInputError("Case bucket is invalid")
        return {
            "cases": self.store.list_cases(user_id, str(bucket)),
            "next_cursor": None,
        }

    def get_case(self, user_id: str, case_id: object) -> dict[str, object]:
        valid_case_id = _identifier(case_id, "case_id")
        result = self.store.get_case(user_id, valid_case_id)
        if result is None:
            raise WorkspaceNotFound("Case was not found")
        return result

    def create_direct_case(
        self,
        user_id: str,
        *,
        prompt: object,
        idempotency_key: object,
    ) -> dict[str, object]:
        valid_prompt = _text(prompt, "prompt", maximum=4000)
        key = _idempotency_key(idempotency_key)
        case_id = _stable_id("case", user_id, key)
        evidence_ref = f"direct:{hashlib.sha256(valid_prompt.encode()).hexdigest()}"
        now = _now()
        case, created = self.store.create_direct_case(
            user_id,
            case_id=case_id,
            prompt=valid_prompt,
            evidence_ref=evidence_ref,
            now=now,
        )
        if created or case.get("status") == "PREPARING":
            self.queue.send(
                user_id=user_id,
                case_id=case_id,
                connector="direct",
                plan_version=int(case.get("requested_plan_version") or 1),
            )
        return {"case_id": case_id, "status": str(case["status"])}

    def convert_candidates(
        self,
        user_id: str,
        *,
        candidate_ids: object,
        expected_versions: object,
        idempotency_key: object,
    ) -> dict[str, object]:
        ids = _identifier_list(
            candidate_ids,
            "candidate_ids",
            maximum=MAX_SELECTED_CANDIDATES,
        )
        versions = _version_list(expected_versions, expected=len(ids))
        key = _idempotency_key(idempotency_key)
        case_id = _stable_id("case", user_id, key)
        case, created = self.store.convert_candidates(
            user_id,
            case_id=case_id,
            candidate_ids=ids,
            expected_versions=versions,
            now=_now(),
        )
        if created or case.get("status") == "PREPARING":
            plan_version = int(case.get("requested_plan_version") or 1)
            # Later generations come from Case messages using the direct queue envelope.
            self.queue.send(
                user_id=user_id,
                case_id=case_id,
                connector="google" if plan_version == 1 else "direct",
                plan_version=plan_version,
            )
        return {"case_id": case_id}

    def post_message(
        self,
        user_id: str,
        *,
        case_id: object,
        text: object,
        expected_version: object,
        idempotency_key: object,
    ) -> dict[str, object]:
        valid_case_id = _identifier(case_id, "case_id")
        valid_text = _text(text, "text", maximum=4000)
        version = _version(expected_version)
        key = _idempotency_key(idempotency_key)
        message_id = _stable_id("message", user_id, key)
        evidence_ref = f"message:{hashlib.sha256(valid_text.encode()).hexdigest()}"
        case, plan_version = self.store.add_message(
            user_id,
            case_id=valid_case_id,
            text=valid_text,
            expected_version=version,
            message_id=message_id,
            evidence_ref=evidence_ref,
            now=_now(),
        )
        self.queue.send(
            user_id=user_id,
            case_id=valid_case_id,
            connector="direct",
            plan_version=plan_version,
        )
        return {
            "message_id": message_id,
            "planning_job_id": f"plan-{valid_case_id}-{plan_version}",
            "case": case,
        }

    def retry(
        self, user_id: str, *, case_id: object, expected_version: object
    ) -> dict[str, object]:
        valid_case_id, version = (
            _identifier(case_id, "case_id"),
            _version(expected_version),
        )
        current = self.store.get_case(user_id, valid_case_id)
        if current is None:
            raise WorkspaceNotFound("Case was not found")
        if (
            current.get("status") == "DECISION_REQUIRED"
            and isinstance(current.get("plan"), dict)
            and not current["plan"].get("actions")
        ):
            case, plan_version = self.store.retry_preparation(
                user_id, case_id=valid_case_id, expected_version=version, now=_now()
            )
            try:
                self.queue.send(
                    user_id=user_id,
                    case_id=valid_case_id,
                    connector="google"
                    if "google" in case.get("providers", [])
                    else "direct",
                    plan_version=plan_version,
                )
            except Exception:
                self.store.preparation_dispatch_failed(
                    user_id,
                    case_id=valid_case_id,
                    plan_version=plan_version,
                    now=_now(),
                )
                raise
            return case
        if self.execution is None:
            raise WorkspaceNotReady("Action execution is unavailable")
        return self.execution.retry(
            user_id,
            case_id=valid_case_id,
            expected_version=version,
        )

    def decide(
        self,
        user_id: str,
        *,
        case_id: object,
        decision: object,
        expected_version: object,
        plan_version: object = None,
        plan_hash: object = None,
        grant_mode: object = None,
    ) -> dict[str, object]:
        valid_case_id = _identifier(case_id, "case_id")
        version = _version(expected_version)
        if decision == "APPROVE":
            if self.execution is not None:
                return self.execution.approve(
                    user_id,
                    case_id=valid_case_id,
                    expected_version=version,
                    plan_version=plan_version,
                    plan_hash=plan_hash,
                    grant_mode=grant_mode,
                )
            raise WorkspaceNotReady(
                "External Action approval is implemented in checklist Item 8"
            )
        if decision == "DEFER":
            target, label, body = (
                "PAUSED",
                "Review later",
                "You deferred this task.",
            )
        elif decision in {"REJECT", "STOP"}:
            target, label, body = (
                "STOPPED",
                "Task stopped",
                "You stopped the remaining work.",
            )
        else:
            raise WorkspaceInputError("Case decision is invalid")
        return self.store.transition_case(
            user_id,
            case_id=valid_case_id,
            expected_version=version,
            status=target,
            label=label,
            body=body,
            now=_now(),
        )


class DynamoWorkspaceStore:
    def __init__(self, table_name: str, client: Any) -> None:
        self._table_name = table_name
        self._client = client

    @classmethod
    def from_environment(cls) -> DynamoWorkspaceStore:
        import boto3

        return cls(_required_environment("MAIN_TABLE_NAME"), boto3.client("dynamodb"))

    def list_cases(self, user_id: str, bucket: str) -> list[dict[str, object]]:
        response = self._client.query(
            TableName=self._table_name,
            IndexName="GSI1",
            KeyConditionExpression="#pk=:pk",
            ExpressionAttributeNames={"#pk": "GSI1PK"},
            ExpressionAttributeValues={
                ":pk": {"S": f"USER#{user_id}#CASE#{bucket.upper()}"}
            },
            ScanIndexForward=False,
            Limit=MAX_CASES + 1,
        )
        items = response.get("Items")
        if not isinstance(items, list):
            raise TypeError("Case query is invalid")
        if len(items) > MAX_CASES or response.get("LastEvaluatedKey"):
            raise RuntimeError("Case read exceeds the bounded mobile view")
        return [_case_summary_from_item(item) for item in items]

    def get_case(self, user_id: str, case_id: str) -> dict[str, object] | None:
        response = self._client.query(
            TableName=self._table_name,
            KeyConditionExpression="#pk=:pk",
            ExpressionAttributeNames={"#pk": "PK"},
            ExpressionAttributeValues={":pk": {"S": f"CASE#{case_id}"}},
            ConsistentRead=True,
            Limit=101,
        )
        items = response.get("Items")
        if not isinstance(items, list) or not items:
            return None
        if len(items) > 100 or response.get("LastEvaluatedKey"):
            raise RuntimeError("Case detail exceeds the bounded mobile view")
        meta = next(
            (
                item
                for item in items
                if isinstance(item, Mapping) and _string(item, "SK") == "META"
            ),
            None,
        )
        if not isinstance(meta, Mapping) or _string(meta, "user_id") != user_id:
            return None
        return _case_detail_from_items(meta, items)

    def retry_preparation(
        self, user_id: str, *, case_id: str, expected_version: int, now: str
    ) -> tuple[dict[str, object], int]:
        current = self.get_case(user_id, case_id)
        if current is None:
            raise WorkspaceNotFound("Case not found")
        if (
            current["version"] != expected_version
            or current["status"] != "DECISION_REQUIRED"
            or not isinstance(current.get("plan"), dict)
            or current["plan"].get("actions")
        ):
            raise WorkspaceConflict("Preparation state changed")
        plan_version = (
            max(
                int(current.get("current_plan_version") or 0),
                int(current.get("requested_plan_version") or 0),
            )
            + 1
        )
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key=_case_key(case_id),
                UpdateExpression="SET #status=:preparing, requested_plan_version=:plan, next_action=:next, updated_at=:now, version=version+:one",
                ConditionExpression="user_id=:user AND version=:version AND #status=:decision AND attribute_not_exists(approved_operation_id)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":preparing": {"S": "PREPARING"},
                    ":plan": {"N": str(plan_version)},
                    ":next": {"S": "Preparing the plan again from its source."},
                    ":now": {"S": now},
                    ":one": {"N": "1"},
                    ":user": {"S": user_id},
                    ":version": {"N": str(expected_version)},
                    ":decision": {"S": "DECISION_REQUIRED"},
                },
            )
        except Exception as error:
            if _error_code(error) == "ConditionalCheckFailedException":
                raise WorkspaceConflict(
                    "An approved execution must be recovered first"
                ) from None
            raise
        result = self.get_case(user_id, case_id)
        if result is None:
            raise WorkspaceConflict("Prepared Case could not be read")
        return result, plan_version

    def preparation_dispatch_failed(
        self, user_id: str, *, case_id: str, plan_version: int, now: str
    ) -> None:
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key=_case_key(case_id),
                UpdateExpression="SET #status=:decision, next_action=:next, updated_at=:now, version=version+:one",
                ConditionExpression="user_id=:user AND #status=:preparing AND requested_plan_version=:plan AND attribute_not_exists(approved_operation_id)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":decision": {"S": "DECISION_REQUIRED"},
                    ":next": {"S": "Could not start preparation. Please try again."},
                    ":now": {"S": now},
                    ":one": {"N": "1"},
                    ":user": {"S": user_id},
                    ":preparing": {"S": "PREPARING"},
                    ":plan": {"N": str(plan_version)},
                },
            )
        except Exception as error:
            if _error_code(error) != "ConditionalCheckFailedException":
                raise

    def create_direct_case(
        self,
        user_id: str,
        *,
        case_id: str,
        prompt: str,
        evidence_ref: str,
        now: str,
    ) -> tuple[dict[str, object], bool]:
        case_item = _case_item(
            user_id=user_id,
            case_id=case_id,
            case_type="DIRECT_DELEGATION",
            goal=prompt[:300],
            summary="Reviewing your request and the actions it needs.",
            risk="LOW",
            providers=[],
            evidence_refs=[evidence_ref],
            now=now,
        )
        evidence = _case_evidence_item(
            user_id=user_id,
            case_id=case_id,
            evidence_id=_stable_id("evidence", user_id, evidence_ref),
            evidence_ref=evidence_ref,
            provider="direct",
            label="Direct request",
            detail=prompt,
            source="direct",
            title=prompt[:200],
            facts=["A request made directly by the user"],
            untrusted_text=prompt,
            now=now,
        )
        created = self._put_new_case(
            user_id,
            case_id,
            case_item,
            [evidence],
            _timeline_item(
                case_id,
                label="Request received",
                body="Created a task and started preparing its plan.",
                state="DONE",
                now=now,
            ),
        )
        if not created:
            existing = self.get_case(user_id, case_id)
            if existing is None:
                raise WorkspaceConflict("Idempotent Case could not be restored")
            return existing, False
        return _case_summary_from_item(case_item), True

    def convert_candidates(
        self,
        user_id: str,
        *,
        case_id: str,
        candidate_ids: list[str],
        expected_versions: list[int],
        now: str,
    ) -> tuple[dict[str, object], bool]:
        existing = self.get_case(user_id, case_id)
        if existing is not None:
            return existing, False

        candidates: list[Mapping[str, Any]] = []
        for candidate_id, version in zip(candidate_ids, expected_versions, strict=True):
            response = self._client.get_item(
                TableName=self._table_name,
                Key=_candidate_key(user_id, candidate_id),
                ConsistentRead=True,
            )
            item = response.get("Item")
            if (
                not isinstance(item, Mapping)
                or _string(item, "candidate_id") != candidate_id
                or _string(item, "status") != "VISIBLE"
                or _integer(item, "version") != version
            ):
                raise WorkspaceConflict("Candidate selection changed")
            candidates.append(item)

        mail_guards: list[dict[str, Any]] = []
        google_candidates = [
            item
            for item in candidates
            if (_string(item, "provider") or "google") == "google"
        ]
        if google_candidates:
            from .mail import DynamoMailStore, configured
            from .mail import _key as mail_key

            mail = DynamoMailStore(self._table_name, self._client)
            mail_state = mail.read(user_id)
            profile = mail_state["profile"]
            scan_id = mail_state["scan"]["scan_id"]
            connection = mail.connection(user_id)
            epoch = _string(connection, "mail_connection_id") or ""
            if (
                not configured(profile)
                or _string(connection, "status") != "CONNECTED"
                or any(
                    _integer(item, "mail_profile_version") != profile["version"]
                    or scan_id is None
                    or _string(item, "mail_scan_id") != scan_id
                    or (_string(item, "mail_connection_id") or "") != epoch
                    for item in google_candidates
                )
            ):
                raise WorkspaceConflict("Mail candidate interests changed")
            mail_guards = [
                mail.connection_guard(user_id, connection),
                {
                    "ConditionCheck": {
                        "TableName": self._table_name,
                        "Key": mail_key(user_id),
                        "ConditionExpression": "profile_version=:version AND scan_id=:scan_id",
                        "ExpressionAttributeValues": {
                            ":version": {"N": str(profile["version"])},
                            ":scan_id": {"S": scan_id},
                        },
                    }
                },
            ]

        risks = {_string(item, "risk") for item in candidates}
        if len(risks) != 1 or None in risks:
            raise WorkspaceConflict("Candidates with different risk must stay separate")
        evidence_refs = list(
            dict.fromkeys(
                reference
                for item in candidates
                for reference in _string_list(item, "evidence_refs")
            )
        )
        if not evidence_refs or len(evidence_refs) > MAX_CASE_EVIDENCE:
            raise WorkspaceConflict("Candidate evidence cannot fit one Case")

        evidence_items: list[dict[str, Any]] = []
        for reference in evidence_refs:
            source_response = self._client.get_item(
                TableName=self._table_name,
                Key={
                    "PK": {"S": f"USER#{user_id}"},
                    "SK": {"S": f"EVIDENCE#{reference}"},
                },
                ConsistentRead=True,
            )
            source = source_response.get("Item")
            if not isinstance(source, Mapping):
                raise WorkspaceConflict("Candidate evidence is unavailable")
            title = _string(source, "title") or "Gmail source"
            facts = _string_list(source, "facts")
            evidence_items.append(
                _case_evidence_item(
                    user_id=user_id,
                    case_id=case_id,
                    evidence_id=_stable_id("evidence", user_id, reference),
                    evidence_ref=reference,
                    provider="google",
                    label=title,
                    detail=" · ".join(facts) or "Gmail source",
                    source=_string(source, "source") or "gmail",
                    title=title,
                    facts=facts,
                    untrusted_text=None,
                    now=now,
                )
            )

        goal = (
            _string(candidates[0], "outcome") or "Prepare selected suggestion"
            if len(candidates) == 1
            else f"Prepare {len(candidates)} selected suggestions together"
        )
        summary = " ".join(
            filter(None, (_string(item, "summary") for item in candidates[:3]))
        )[:2000]
        proposed_actions = [
            action for item in candidates for action in _candidate_actions(item)
        ]
        if not proposed_actions or len(proposed_actions) > MAX_CASE_ACTIONS:
            raise WorkspaceConflict("Candidate has no supported action to prepare")
        required_capabilities = list(
            dict.fromkeys(
                capability
                for item in candidates
                for capability in _string_list(item, "required_capabilities")
            )
        )
        case_item = _case_item(
            user_id=user_id,
            case_id=case_id,
            case_type="CONNECTED_SIGNAL",
            goal=goal[:300],
            summary=summary or "Preparing one task from related sources.",
            risk=str(next(iter(risks))),
            providers=["google"],
            evidence_refs=evidence_refs,
            now=now,
        )
        case_item["why_now"] = {
            "S": _string(candidates[0], "why_now") or "Review the suggested next step."
        }
        if google_candidates:
            account = _string(connection, "account_hash")
            if account:
                case_item["google_account_hash"] = {"S": account}
                case_item["mail_connection_id"] = {"S": epoch}
        case_item["required_capabilities"] = {
            "L": [{"S": value} for value in required_capabilities]
        }
        case_item["requested_actions_json"] = {
            "S": json.dumps(
                proposed_actions,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        }
        transaction: list[dict[str, object]] = [
            *mail_guards,
            {
                "Put": {
                    "TableName": self._table_name,
                    "Item": case_item,
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            },
            *[
                {"Put": {"TableName": self._table_name, "Item": item}}
                for item in evidence_items
            ],
            {
                "Put": {
                    "TableName": self._table_name,
                    "Item": _timeline_item(
                        case_id,
                        label="Suggestion selected",
                        body=f"Combined {len(candidates)} related suggestions into one task.",
                        state="DONE",
                        now=now,
                    ),
                }
            },
        ]
        for item, version in zip(candidates, expected_versions, strict=True):
            candidate_id = _string(item, "candidate_id") or ""
            transaction.append(
                {
                    "Update": {
                        "TableName": self._table_name,
                        "Key": _candidate_key(user_id, candidate_id),
                        "UpdateExpression": (
                            "SET #status=:converted, version=version+:one, updated_at=:now "
                            "REMOVE GSI1PK, GSI1SK"
                        ),
                        "ConditionExpression": "#status=:visible AND version=:version",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": {
                            ":converted": {"S": "CONVERTED"},
                            ":visible": {"S": "VISIBLE"},
                            ":version": {"N": str(version)},
                            ":one": {"N": "1"},
                            ":now": {"S": now},
                        },
                    }
                }
            )
        try:
            self._client.transact_write_items(TransactItems=transaction)
        except Exception as error:
            existing = self.get_case(user_id, case_id)
            if existing is not None:
                return existing, False
            if _error_code(error) == "TransactionCanceledException":
                raise WorkspaceConflict("Candidate selection changed") from error
            raise
        return _case_summary_from_item(case_item), True

    def _local_refinement_guard(
        self, user_id: str, case_id: str, current: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        response = self._client.query(
            TableName=self._table_name,
            KeyConditionExpression="#pk=:pk",
            ExpressionAttributeNames={"#pk": "PK"},
            ExpressionAttributeValues={":pk": {"S": f"CASE#{case_id}"}},
            ConsistentRead=True,
            Limit=MAX_CASES + 1,
        )
        items = response.get("Items", [])
        if len(items) > MAX_CASES or response.get("LastEvaluatedKey"):
            return None
        meta = next((item for item in items if _string(item, "SK") == "META"), {})
        if (
            _string(meta, "user_id") != user_id
            or _integer(meta, "version") != current.get("version")
            or _string(meta, "status") != current.get("status")
            or "approved_operation_id" in meta
            or "approved_approval_id" in meta
            or any(
                _string(item, "entity_type") in {"approval", "action_execution"}
                for item in items
            )
        ):
            return None
        version, plan_hash = (
            _integer(meta, "current_plan_version"),
            _string(meta, "current_plan_hash"),
        )
        if version is None or version < 1 or not plan_hash:
            return None
        record = next(
            (item for item in items if _string(item, "SK") == f"PLAN#{version:06d}"), {}
        )
        encoded = _string(record, "plan_json")
        try:
            plan = json.loads(encoded) if encoded else None
        except (ValueError, TypeError):
            return None
        if (
            not isinstance(plan, dict)
            or plan != current.get("plan")
            or plan.get("version") != version
            or plan.get("hash") != plan_hash
            or _string(record, "plan_hash") != plan_hash
            or plan.get("required_scopes") != []
            or plan.get("available_grant_modes") != []
        ):
            return None
        actions = plan.get("actions")
        if plan.get("local_preparation_status") == "NO_ACTION":
            valid = actions == []
        elif (
            plan.get("local_preparation_status") == "READY"
            and isinstance(actions, list)
            and len(actions) == 1
        ):
            action = actions[0]
            if not isinstance(action, Mapping):
                return None
            parameters = action.get("parameters", {})
            verb = action.get("verb")
            artifact = (
                {
                    "prepare_reply": "REPLY_DRAFT",
                    "prepare_task": "CHECKLIST",
                    "prepare_reminder": "REMINDER",
                }.get(verb)
                if isinstance(verb, str)
                else None
            )
            valid = (
                action.get("connector") == "quietpilot"
                and artifact is not None
                and action.get("status") == "SUCCEEDED"
                and action.get("required_scopes") == []
                and action.get("reversible") is True
                and action.get("verified") is False
                and action.get("result_ref")
                == f"local-preparation:{case_id}:{version}:{action.get('action_id')}"
                and isinstance(parameters, Mapping)
                and parameters.get("artifact_type") == artifact
                and isinstance(parameters.get("content"), str)
                and bool(parameters["content"].strip())
            )
        else:
            valid = False
        if not valid:
            return None
        return {
            "ConditionCheck": {
                "TableName": self._table_name,
                "Key": {
                    "PK": {"S": f"CASE#{case_id}"},
                    "SK": {"S": f"PLAN#{version:06d}"},
                },
                "ConditionExpression": "plan_json=:observed_plan AND plan_hash=:observed_hash",
                "ExpressionAttributeValues": {
                    ":observed_plan": {"S": encoded},
                    ":observed_hash": {"S": plan_hash},
                },
            }
        }

    def add_message(
        self,
        user_id: str,
        *,
        case_id: str,
        text: str,
        expected_version: int,
        message_id: str,
        evidence_ref: str,
        now: str,
    ) -> tuple[dict[str, object], int]:
        current = self.get_case(user_id, case_id)
        if current is None:
            raise WorkspaceNotFound("Case was not found")
        if any(
            isinstance(message, Mapping) and message.get("message_id") == message_id
            for message in current.get("messages", [])
        ):
            replay_plan_version = current.get("requested_plan_version")
            if type(replay_plan_version) is not int or replay_plan_version < 1:
                raise WorkspaceConflict("Replayed Case plan version is unavailable")
            return current, replay_plan_version
        if current["version"] != expected_version:
            raise WorkspaceConflict("Case changed before the message was applied")
        local_result = isinstance(current.get("plan"), dict) and current["plan"].get(
            "local_preparation_status"
        ) in {"READY", "NO_ACTION"}
        local_candidate = local_result and current.get("status") in {
            "COMPLETED",
            "PREPARING",
        }
        local_guard = (
            self._local_refinement_guard(user_id, case_id, current)
            if local_candidate
            else None
        )
        blocked = current.get("status") in {
            "APPROVED",
            "QUEUED",
            "RUNNING",
            "VERIFYING",
            "COMPLETED",
            "STOPPED",
        } or any(
            action.get("status") in {"RUNNING", "VERIFYING", "SUCCEEDED", "FAILED"}
            for action in current.get("actions", [])
        )
        if (local_result or blocked) and local_guard is None:
            raise WorkspaceConflict(
                "Confirm the existing action result before changing its plan"
            )
        evidence_refs = [
            str(item["evidence_ref"])
            for item in current.get("evidence", [])
            if isinstance(item, Mapping) and isinstance(item.get("evidence_ref"), str)
        ]
        if evidence_ref not in evidence_refs:
            evidence_refs.append(evidence_ref)
        if len(evidence_refs) > MAX_CASE_EVIDENCE:
            raise WorkspaceConflict("Case evidence limit is reached")
        plan_version = (
            max(
                int(current.get("current_plan_version") or 0),
                int(current.get("requested_plan_version") or 0),
            )
            + 1
        )
        message_item = {
            "PK": {"S": f"CASE#{case_id}"},
            "SK": {"S": f"MESSAGE#{now}#{message_id}"},
            "entity_type": {"S": "message"},
            "message_id": {"S": message_id},
            "author": {"S": "USER"},
            "text": {"S": text},
            "created_at": {"S": now},
        }
        evidence_item = _case_evidence_item(
            user_id=user_id,
            case_id=case_id,
            evidence_id=_stable_id("evidence", user_id, evidence_ref),
            evidence_ref=evidence_ref,
            provider="direct",
            label="Task update",
            detail=text,
            source="direct",
            title=text[:200],
            facts=["The user’s update to this task"],
            untrusted_text=text,
            now=now,
        )
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table_name,
                            "Key": _case_key(case_id),
                            "UpdateExpression": (
                                "SET #status=:preparing, evidence_refs=:evidence, "
                                "requested_plan_version=:plan, next_action=:next, "
                                "updated_at=:now, version=version+:one, "
                                "GSI1PK=:gsi_pk, GSI1SK=:gsi_sk"
                            ),
                            "ConditionExpression": "user_id=:user AND version=:version"
                            + (
                                " AND #status=:observed_status AND current_plan_version=:observed_version AND current_plan_hash=:observed_hash AND attribute_not_exists(approved_operation_id) AND attribute_not_exists(approved_approval_id)"
                                if local_guard is not None
                                else ""
                            ),
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": {
                                ":preparing": {"S": "PREPARING"},
                                ":evidence": {
                                    "L": [{"S": ref} for ref in evidence_refs]
                                },
                                ":plan": {"N": str(plan_version)},
                                ":next": {"S": "Updating the plan with your changes."},
                                ":now": {"S": now},
                                ":one": {"N": "1"},
                                ":user": {"S": user_id},
                                ":version": {"N": str(expected_version)},
                                ":gsi_sk": {"S": _case_sort_key(50, now, case_id)},
                                ":gsi_pk": {"S": f"USER#{user_id}#CASE#ACTIVE"},
                                **(
                                    {
                                        ":observed_status": {"S": current["status"]},
                                        ":observed_version": {
                                            "N": str(current["current_plan_version"])
                                        },
                                        ":observed_hash": {
                                            "S": current["current_plan_hash"]
                                        },
                                    }
                                    if local_guard is not None
                                    else {}
                                ),
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": message_item,
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    {"Put": {"TableName": self._table_name, "Item": evidence_item}},
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": _timeline_item(
                                case_id,
                                label="Plan update requested",
                                body="Added your message to this task’s sources.",
                                state="DONE",
                                now=now,
                            ),
                        }
                    },
                    *([local_guard] if local_guard is not None else []),
                ]
            )
        except Exception as error:
            if _error_code(error) == "TransactionCanceledException":
                replay = self.get_case(user_id, case_id)
                if replay is not None and any(
                    isinstance(message, Mapping)
                    and message.get("message_id") == message_id
                    for message in replay.get("messages", [])
                ):
                    return replay, plan_version
                raise WorkspaceConflict(
                    "Case changed before the message was applied"
                ) from error
            raise
        updated = self.get_case(user_id, case_id)
        if updated is None:
            raise WorkspaceConflict("Updated Case could not be read")
        return updated, plan_version

    def transition_case(
        self,
        user_id: str,
        *,
        case_id: str,
        expected_version: int,
        status: str,
        label: str,
        body: str,
        now: str,
    ) -> dict[str, object]:
        bucket = "HISTORY" if status in HISTORY_STATUSES else "ACTIVE"
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table_name,
                            "Key": _case_key(case_id),
                            "UpdateExpression": (
                                "SET #status=:status, next_action=:next, updated_at=:now, "
                                "version=version+:one, GSI1PK=:gsi_pk, GSI1SK=:gsi_sk"
                            ),
                            "ConditionExpression": "user_id=:user AND version=:version",
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": {
                                ":status": {"S": status},
                                ":next": {
                                    "S": "Available in history."
                                    if status == "STOPPED"
                                    else "You can resume when ready."
                                },
                                ":now": {"S": now},
                                ":one": {"N": "1"},
                                ":user": {"S": user_id},
                                ":version": {"N": str(expected_version)},
                                ":gsi_pk": {"S": f"USER#{user_id}#CASE#{bucket}"},
                                ":gsi_sk": {"S": _case_sort_key(50, now, case_id)},
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": _timeline_item(
                                case_id,
                                label=label,
                                body=body,
                                state="DONE",
                                now=now,
                            ),
                        }
                    },
                ]
            )
        except Exception as error:
            if _error_code(error) == "TransactionCanceledException":
                raise WorkspaceConflict(
                    "Case changed before the decision was applied"
                ) from error
            raise
        result = self.get_case(user_id, case_id)
        if result is None:
            raise WorkspaceConflict("Updated Case could not be read")
        return result

    def _put_new_case(
        self,
        user_id: str,
        case_id: str,
        case_item: dict[str, Any],
        evidence_items: list[dict[str, Any]],
        event_item: dict[str, Any],
    ) -> bool:
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": case_item,
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    *[
                        {"Put": {"TableName": self._table_name, "Item": item}}
                        for item in evidence_items
                    ],
                    {"Put": {"TableName": self._table_name, "Item": event_item}},
                ]
            )
            return True
        except Exception as error:
            if _error_code(error) == "TransactionCanceledException":
                existing = self.get_case(user_id, case_id)
                if existing is not None:
                    return False
            raise


class SqsCasePreparationQueue:
    def __init__(self, queue_url: str, client: Any) -> None:
        self._queue_url = queue_url
        self._client = client

    @classmethod
    def from_environment(cls) -> SqsCasePreparationQueue:
        import boto3

        return cls(_required_environment("WORK_QUEUE_URL"), boto3.client("sqs"))

    def send(
        self,
        *,
        user_id: str,
        case_id: str,
        connector: str,
        plan_version: int,
    ) -> None:
        now = _now()
        event_id = str(uuid.uuid4())
        envelope = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": "DIRECT_REQUEST_RECEIVED",
            "user_id": user_id,
            "connector": connector,
            "occurred_at": now,
            "dedupe_key": f"case-plan:{case_id}:{plan_version}",
            "trace_id": str(uuid.uuid4()),
            "payload": {"case_id": case_id, "plan_version": plan_version},
        }
        self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
        )


def default_workspace_service() -> WorkspaceService:
    from .calendar_approval import CalendarApprovalService
    from .connections import SqsWorkQueue

    store = DynamoWorkspaceStore.from_environment()
    return WorkspaceService(
        store=store,
        queue=SqsCasePreparationQueue.from_environment(),
        execution=CalendarApprovalService(store, SqsWorkQueue.from_environment()),
    )


def _stable_id(kind: str, user_id: str, key: str) -> str:
    return f"{kind}-{uuid.uuid5(uuid.NAMESPACE_URL, f'quietpilot:{user_id}:{key}').hex}"


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise WorkspaceInputError(f"{name} is invalid")
    return value


def _identifier_list(value: object, name: str, *, maximum: int) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise WorkspaceInputError(f"{name} is invalid")
    result = [_identifier(item, name) for item in value]
    if len(result) != len(set(result)):
        raise WorkspaceInputError(f"{name} contains duplicates")
    return result


def _version(value: object) -> int:
    if type(value) is not int or value < 1:
        raise WorkspaceInputError("expected_version is invalid")
    return value


def _version_list(value: object, *, expected: int) -> list[int]:
    if not isinstance(value, list) or len(value) != expected:
        raise WorkspaceInputError("expected_versions do not match candidates")
    return [_version(item) for item in value]


def _text(value: object, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise WorkspaceInputError(f"{name} is invalid")
    return value.strip()


def _idempotency_key(value: object) -> str:
    if not isinstance(value, str) or IDEMPOTENCY_PATTERN.fullmatch(value) is None:
        raise WorkspaceInputError("Idempotency-Key is invalid")
    return value


def _candidate_actions(item: Mapping[str, Any]) -> list[dict[str, object]]:
    encoded = _string(item, "proposed_actions_json")
    if encoded is None:
        return []
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise WorkspaceConflict("Candidate action payload is invalid") from error
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 3
        or any(not isinstance(action, dict) for action in value)
    ):
        raise WorkspaceConflict("Candidate action payload is invalid")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    nested = response.get("Error")
    return str(nested.get("Code")) if isinstance(nested, Mapping) else None


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


# Keep the established public entry points available after extraction.
__all__ = [
    "ACTIVE_STATUSES",
    "CASE_TYPES",
    "HISTORY_STATUSES",
    "IDEMPOTENCY_PATTERN",
    "MAX_CASES",
    "MAX_CASE_ACTIONS",
    "MAX_CASE_EVIDENCE",
    "MAX_SELECTED_CANDIDATES",
    "RISKS",
    "CasePreparationQueue",
    "DynamoWorkspaceStore",
    "SqsCasePreparationQueue",
    "WorkspaceConflict",
    "WorkspaceInputError",
    "WorkspaceNotFound",
    "WorkspaceNotReady",
    "WorkspaceService",
    "WorkspaceStore",
    "default_workspace_service",
]
