"""DynamoDB persistence and cursor-bound leases for Google connection jobs."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

ACTION_READY_DISCOVERY_REVISION = 1

HISTORY_SYNC_LEASE_SECONDS = 150


class HistorySyncInProgress(RuntimeError):
    """A newer Gmail notification must retry after the active user sync."""


class CandidateRefreshConflict(RuntimeError):
    """User feedback changed while a mail page's Candidate writes were prepared."""


@dataclass(frozen=True, slots=True)
class HistorySyncClaim:
    start_history_id: str
    token: str


class DynamoConnectionWriter:
    def __init__(
        self,
        table_name: str,
        client: Any,
        *,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._table_name = table_name
        self._client = client
        self._clock = clock
        self._token_factory = token_factory

    @classmethod
    def from_environment(cls) -> DynamoConnectionWriter:
        import boto3

        return cls(
            _required_environment("MAIN_TABLE_NAME"),
            boto3.client("dynamodb"),
        )

    def write(
        self,
        user_id: str,
        *,
        status: str,
        granted_scopes: list[str],
        scan_progress: int,
        discovery_revision: int,
        details: Mapping[str, object] | None = None,
        scan_id: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        values: dict[str, object] = {
            ":entity": {"S": "connection"},
            ":provider": {"S": "google"},
            ":label": {"S": "Google"},
            ":status": {"S": status},
            ":scopes": {"L": [{"S": scope} for scope in granted_scopes]},
            ":lookback": {"N": "7"},
            ":progress": {"N": str(scan_progress)},
            ":discovery_revision": {"N": str(discovery_revision)},
            ":checked": {"S": now},
            ":user": {"S": user_id},
            ":zero": {"N": "0"},
            ":one": {"N": "1"},
        }
        expression = (
            "SET entity_type=:entity, provider=:provider, label=:label, "
            "#status=:status, granted_scopes=:scopes, lookback_days=:lookback, "
            "scan_progress=:progress, discovery_revision=:discovery_revision, "
            "last_checked_at=:checked, "
            "user_id=:user, "
            "version=if_not_exists(version,:zero)+:one"
        )
        if details:
            user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
            values.update(
                {
                    ":estimate": {"N": str(details["recent_message_estimate"])},
                    ":history": {"S": str(details["history_id"])},
                    ":expiration": {"N": str(details["watch_expiration"])},
                    ":renewed": {"S": now},
                    ":sync_mode": {"S": "INITIAL_7_DAY"},
                    ":account_hash": {"S": str(details["account_hash"])},
                    ":gsi_pk": {"S": f"GOOGLE_ACCOUNT#{details['account_hash']}"},
                    ":gsi_sk": {"S": "CONNECTION#google"},
                    ":gsi2_pk": {"S": "CONNECTION#google#CONNECTED"},
                    ":gsi2_sk": {"S": f"{details['watch_expiration']}#{user_hash}"},
                }
            )
            expression += (
                ", recent_message_estimate=:estimate, gmail_history_id=:history, "
                "watch_expiration=:expiration, account_hash=:account_hash, "
                "watch_renewed_at=:renewed, last_sync_mode=:sync_mode, "
                "GSI1PK=:gsi_pk, GSI1SK=:gsi_sk, "
                "GSI2PK=:gsi2_pk, GSI2SK=:gsi2_sk"
            )
            if scan_id is not None:
                values[":scan_id"] = {"S": scan_id}
                expression += ", gmail_scan_id=:scan_id"
            expression += " REMOVE gmail_sync_token, gmail_sync_expires_at, error_code"
            if scan_id is None:
                expression += ", gmail_scan_id"
        else:
            expression += (
                " REMOVE recent_message_estimate, gmail_history_id, watch_expiration, "
                "watch_renewed_at, last_sync_mode, account_hash, "
                "GSI1PK, GSI1SK, GSI2PK, GSI2SK, "
                "gmail_sync_token, gmail_sync_expires_at, gmail_scan_id, "
                "gmail_scan_unresolved_count, error_code"
            )
        self._client.update_item(
            TableName=self._table_name,
            Key={
                "PK": {"S": f"USER#{user_id}"},
                "SK": {"S": "CONNECTION#google"},
            },
            UpdateExpression=expression,
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=values,
        )

    def get_history_cursor(self, user_id: str) -> str | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=_connection_key(user_id),
            ConsistentRead=True,
            ProjectionExpression="#status,gmail_history_id",
            ExpressionAttributeNames={"#status": "status"},
        )
        item = response.get("Item")
        if not isinstance(item, Mapping):
            return None
        if _dynamo_text(item, "status") != "CONNECTED":
            return None
        value = _dynamo_text(item, "gmail_history_id")
        return value if value is not None and value.isdigit() else None

    def is_active_scan(self, user_id: str, *, scan_id: str) -> bool:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=_connection_key(user_id),
            ConsistentRead=True,
            ProjectionExpression="#status,gmail_scan_id",
            ExpressionAttributeNames={"#status": "status"},
        )
        item = response.get("Item")
        return (
            isinstance(item, Mapping)
            and _dynamo_text(item, "status") == "CONNECTED"
            and _dynamo_text(item, "gmail_scan_id") == scan_id
        )

    def claim_history_sync(
        self,
        user_id: str,
        *,
        notified_history_id: str,
    ) -> HistorySyncClaim | None:
        start_history_id = self.get_history_cursor(user_id)
        if start_history_id is None or int(notified_history_id) <= int(
            start_history_id
        ):
            return None

        now = int(self._clock())
        token = self._token_factory()
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key=_connection_key(user_id),
                UpdateExpression=(
                    "SET gmail_sync_token=:token, gmail_sync_expires_at=:expiry"
                ),
                ConditionExpression=(
                    "#status=:connected AND gmail_history_id=:start AND "
                    "(attribute_not_exists(gmail_sync_token) OR "
                    "gmail_sync_expires_at<=:now)"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":connected": {"S": "CONNECTED"},
                    ":start": {"S": start_history_id},
                    ":token": {"S": token},
                    ":expiry": {"N": str(now + HISTORY_SYNC_LEASE_SECONDS)},
                    ":now": {"N": str(now)},
                },
            )
        except self._client.exceptions.ConditionalCheckFailedException as error:
            current_history_id = self.get_history_cursor(user_id)
            if current_history_id is None or int(notified_history_id) <= int(
                current_history_id
            ):
                return None
            raise HistorySyncInProgress(
                "another Gmail history sync is in progress"
            ) from error
        return HistorySyncClaim(start_history_id=start_history_id, token=token)

    def release_history_sync(self, user_id: str, *, token: str) -> None:
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key=_connection_key(user_id),
                UpdateExpression=("REMOVE gmail_sync_token, gmail_sync_expires_at"),
                ConditionExpression="gmail_sync_token=:token",
                ExpressionAttributeValues={":token": {"S": token}},
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return

    def persist_sync(
        self,
        user_id: str,
        *,
        start_history_id: str,
        history_id: str,
        recovery_mode: str,
        evidence: list[dict[str, object]],
        candidates: list[dict[str, object]],
        sync_token: str | None = None,
    ) -> list[str]:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        transaction: list[dict[str, object]] = []
        for record in evidence:
            evidence_ref = str(record["ref"])
            transaction.append(
                {
                    "Update": {
                        "TableName": self._table_name,
                        "Key": _evidence_key(user_id, evidence_ref),
                        "UpdateExpression": (
                            "SET entity_type=if_not_exists(entity_type,:entity), "
                            "user_id=if_not_exists(user_id,:user), "
                            "evidence_ref=if_not_exists(evidence_ref,:ref), "
                            "revision=if_not_exists(revision,:revision), "
                            "#source=if_not_exists(#source,:source), "
                            "title=if_not_exists(title,:title), "
                            "facts=if_not_exists(facts,:facts), "
                            "created_at=if_not_exists(created_at,:created)"
                        ),
                        "ExpressionAttributeNames": {"#source": "source"},
                        "ExpressionAttributeValues": {
                            ":entity": {"S": "evidence"},
                            ":user": {"S": user_id},
                            ":ref": {"S": evidence_ref},
                            ":revision": {"N": str(record["revision"])},
                            ":source": {"S": str(record["source"])},
                            ":title": {"S": str(record["title"])},
                            ":facts": {
                                "L": [
                                    {"S": str(fact)} for fact in record.get("facts", [])
                                ]
                            },
                            ":created": {"S": now},
                        },
                    }
                }
            )

        candidate_ids: list[str] = []
        for candidate in candidates:
            fingerprint = _candidate_fingerprint(candidate)
            candidate_id = fingerprint[:32]
            candidate_ids.append(candidate_id)
            group_id = str(candidate["primary_group_hint"])
            proposed_actions = json.dumps(
                candidate["proposed_actions"],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            candidate_values: dict[str, object] = {
                ":entity": {"S": "candidate"},
                ":user": {"S": user_id},
                ":candidate_id": {"S": candidate_id},
                ":source_type": {"S": "CONNECTED_SIGNAL"},
                ":outcome": {"S": str(candidate["outcome"])},
                ":summary": {"S": str(candidate["summary"])},
                ":why_now": {"S": str(candidate["why_now"])},
                ":opportunity_type": {"S": str(candidate["opportunity_type"])},
                ":evidence_refs": {
                    "L": [{"S": str(value)} for value in candidate["evidence_refs"]]
                },
                ":confidence": {"N": str(candidate["confidence"])},
                ":group": {"S": group_id},
                ":tags": {
                    "L": [{"S": str(value)} for value in candidate.get("tags", [])]
                },
                ":risk": {"S": str(candidate["risk"])},
                ":capabilities": {
                    "L": [
                        {"S": str(value)}
                        for value in candidate.get("required_capabilities", [])
                    ]
                },
                ":proposed_actions": {"S": proposed_actions},
                ":fingerprint": {"S": fingerprint},
                ":visible": {"S": "VISIBLE"},
                ":one": {"N": "1"},
                ":created": {"S": now},
                ":gsi_pk": {"S": f"USER#{user_id}#CANDIDATE#VISIBLE#{group_id}"},
                ":gsi_sk": {"S": f"{now}#{candidate_id}"},
            }
            transaction.append(
                {
                    "Update": {
                        "TableName": self._table_name,
                        "Key": _candidate_key(user_id, candidate_id),
                        "UpdateExpression": (
                            "SET entity_type=if_not_exists(entity_type,:entity), "
                            "user_id=if_not_exists(user_id,:user), "
                            "candidate_id=if_not_exists(candidate_id,:candidate_id), "
                            "source_type=if_not_exists(source_type,:source_type), "
                            "outcome=if_not_exists(outcome,:outcome), "
                            "summary=if_not_exists(summary,:summary), "
                            "why_now=if_not_exists(why_now,:why_now), "
                            "opportunity_type=if_not_exists(opportunity_type,:opportunity_type), "
                            "evidence_refs=if_not_exists(evidence_refs,:evidence_refs), "
                            "confidence=if_not_exists(confidence,:confidence), "
                            "primary_group_id=if_not_exists(primary_group_id,:group), "
                            "tags=if_not_exists(tags,:tags), "
                            "risk=if_not_exists(risk,:risk), "
                            "required_capabilities=if_not_exists(required_capabilities,:capabilities), "
                            "proposed_actions_json=if_not_exists(proposed_actions_json,:proposed_actions), "
                            "fingerprint=if_not_exists(fingerprint,:fingerprint), "
                            "#status=if_not_exists(#status,:visible), "
                            "version=if_not_exists(version,:one), "
                            "created_at=if_not_exists(created_at,:created), "
                            "updated_at=if_not_exists(updated_at,:created), "
                            "GSI1PK=if_not_exists(GSI1PK,:gsi_pk), "
                            "GSI1SK=if_not_exists(GSI1SK,:gsi_sk)"
                        ),
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": candidate_values,
                    }
                }
            )

        connection_expression = (
            "SET gmail_history_id=:history, last_checked_at=:checked, "
            "last_sync_mode=:mode, discovery_revision=:discovery_revision, "
            "version=if_not_exists(version,:zero)+:one"
        )
        condition_expression = "#status=:connected AND gmail_history_id=:start"
        connection_values: dict[str, object] = {
            ":connected": {"S": "CONNECTED"},
            ":start": {"S": start_history_id},
            ":history": {"S": history_id},
            ":checked": {"S": now},
            ":mode": {"S": recovery_mode},
            ":discovery_revision": {"N": str(ACTION_READY_DISCOVERY_REVISION)},
            ":zero": {"N": "0"},
            ":one": {"N": "1"},
        }
        if sync_token is not None:
            connection_expression += " REMOVE gmail_sync_token, gmail_sync_expires_at"
            condition_expression += " AND gmail_sync_token=:sync_token"
            connection_values[":sync_token"] = {"S": sync_token}
        connection_update: dict[str, object] = {
            "TableName": self._table_name,
            "Key": _connection_key(user_id),
            "UpdateExpression": connection_expression,
            "ConditionExpression": condition_expression,
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": connection_values,
        }
        transaction.append({"Update": connection_update})
        self._client.transact_write_items(TransactItems=transaction)
        return candidate_ids

    def persist_scan_page(
        self,
        user_id: str,
        *,
        scan_id: str,
        processed_message_count: int,
        recent_message_estimate: int,
        unresolved_evidence_count: int,
        evidence: list[dict[str, object]],
        candidates: list[dict[str, object]],
        complete: bool,
    ) -> list[str]:
        progress = _scan_progress(
            processed_message_count,
            recent_message_estimate,
            complete=complete and unresolved_evidence_count == 0,
        )
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        transaction = self._signal_updates(
            user_id,
            evidence=evidence,
            candidates=candidates,
            now=now,
        )
        candidate_ids = [
            _candidate_fingerprint(candidate)[:32] for candidate in candidates
        ]
        expression = (
            "SET scan_progress=:progress, recent_message_estimate=:estimate, "
            "version=if_not_exists(version,:zero)+:one"
        )
        values: dict[str, object] = {
            ":progress": {"N": str(progress)},
            ":estimate": {"N": str(recent_message_estimate)},
            ":zero": {"N": "0"},
            ":one": {"N": "1"},
            ":connected": {"S": "CONNECTED"},
            ":scan_id": {"S": scan_id},
        }
        if complete:
            final_status = "CONNECTED" if unresolved_evidence_count == 0 else "ERROR"
            expression += (
                ", #status=:final_status, discovery_revision=:revision, "
                "last_checked_at=:checked, last_sync_mode=:mode"
            )
            values.update(
                {
                    ":final_status": {"S": final_status},
                    ":revision": {
                        "N": str(
                            ACTION_READY_DISCOVERY_REVISION
                            if unresolved_evidence_count == 0
                            else 0
                        )
                    },
                    ":checked": {"S": now},
                    ":mode": {"S": "INITIAL_7_DAY"},
                }
            )
            if unresolved_evidence_count == 0:
                expression += (
                    " REMOVE gmail_scan_id, gmail_scan_unresolved_count, error_code"
                )
            else:
                expression += ", error_code=:error REMOVE gmail_scan_id, gmail_scan_unresolved_count"
                values[":error"] = {"S": "DISCOVERY_INCOMPLETE"}
        else:
            expression += ", gmail_scan_unresolved_count=:unresolved"
            values[":unresolved"] = {"N": str(unresolved_evidence_count)}
        transaction.append(
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": _connection_key(user_id),
                    "UpdateExpression": expression,
                    "ConditionExpression": (
                        "#status=:connected AND gmail_scan_id=:scan_id"
                    ),
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": values,
                }
            }
        )
        self._client.transact_write_items(TransactItems=transaction)
        return candidate_ids

    def _signal_updates(
        self,
        user_id: str,
        *,
        evidence: list[dict[str, object]],
        candidates: list[dict[str, object]],
        now: str,
        mail_profile_version: int | None = None,
        mail_scan_id: str | None = None,
        mail_connection_id: str | None = None,
    ) -> list[dict[str, object]]:
        transaction: list[dict[str, object]] = []
        suppression_items: dict[str, Mapping[str, Any]] = {}
        suppression_guards: dict[str, dict[str, Any]] = {}
        for record in evidence:
            evidence_ref = str(record["ref"])
            transaction.append(
                {
                    "Update": {
                        "TableName": self._table_name,
                        "Key": _evidence_key(user_id, evidence_ref),
                        "UpdateExpression": (
                            "SET entity_type=if_not_exists(entity_type,:entity), "
                            "user_id=if_not_exists(user_id,:user), "
                            "evidence_ref=if_not_exists(evidence_ref,:ref), "
                            "revision=if_not_exists(revision,:revision), "
                            "#source=if_not_exists(#source,:source), "
                            "title=if_not_exists(title,:title), "
                            "facts=if_not_exists(facts,:facts), "
                            "created_at=if_not_exists(created_at,:created)"
                        ),
                        "ExpressionAttributeNames": {"#source": "source"},
                        "ExpressionAttributeValues": {
                            ":entity": {"S": "evidence"},
                            ":user": {"S": user_id},
                            ":ref": {"S": evidence_ref},
                            ":revision": {"N": str(record["revision"])},
                            ":source": {"S": str(record["source"])},
                            ":title": {"S": str(record["title"])},
                            ":facts": {
                                "L": [
                                    {"S": str(fact)} for fact in record.get("facts", [])
                                ]
                            },
                            ":created": {"S": now},
                        },
                    }
                }
            )
        for candidate in candidates:
            fingerprint = _candidate_fingerprint(candidate)
            candidate_id = fingerprint[:32]
            group_id = str(candidate["primary_group_hint"])
            candidate_guard: dict[str, Any] = {}
            if mail_profile_version is not None:
                key = _candidate_key(user_id, candidate_id)
                previous = (
                    self._client.get_item(
                        TableName=self._table_name, Key=key, ConsistentRead=True
                    ).get("Item")
                    or {}
                )
                if previous and (
                    _dynamo_text(previous, "user_id") != user_id
                    or _dynamo_text(previous, "candidate_id") != candidate_id
                    or _dynamo_text(previous, "fingerprint") != fingerprint
                ):
                    raise ValueError("Stored Candidate identity does not match")
                candidate_guard = _snapshot_guard(
                    previous,
                    ("user_id", "candidate_id", "fingerprint", "status", "version"),
                )
                if previous and _dynamo_text(previous, "status") != "VISIBLE":
                    # A user's selected/hidden/converted record is historical input,
                    # not a fresh proposal to rewrite or put back in the visible index.
                    transaction.append(
                        {
                            "ConditionCheck": {
                                "TableName": self._table_name,
                                "Key": key,
                                **candidate_guard,
                            }
                        }
                    )
                    continue
                old_group = _dynamo_text(previous, "primary_group_id")
                if old_group and old_group != group_id:
                    if old_group not in suppression_items:
                        rule_id = hashlib.sha256(
                            f"{user_id}:{old_group}".encode()
                        ).hexdigest()[:24]
                        rule_key = {
                            "PK": {"S": f"USER#{user_id}"},
                            "SK": {"S": f"SUPPRESSION#{rule_id}"},
                        }
                        rule = (
                            self._client.get_item(
                                TableName=self._table_name,
                                Key=rule_key,
                                ConsistentRead=True,
                            ).get("Item")
                            or {}
                        )
                        suppression_items[old_group] = rule
                        suppression_guards[old_group] = {
                            "ConditionCheck": {
                                "TableName": self._table_name,
                                "Key": rule_key,
                                **_snapshot_guard(
                                    rule,
                                    (
                                        "user_id",
                                        "group_id",
                                        "source_candidate_id",
                                        "active",
                                        "version",
                                    ),
                                ),
                            }
                        }
                    rule = suppression_items[old_group]
                    if rule.get("active", {}).get("BOOL") is True and _dynamo_text(
                        rule, "source_candidate_id"
                    ) not in {None, candidate_id}:
                        # Refresh hidden-by-rule content without escaping the user's
                        # suppression group. Undo can then reveal the current proposal.
                        group_id = old_group
            proposed_actions = json.dumps(
                candidate["proposed_actions"],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            proposal_fields = {
                "outcome": ":outcome",
                "summary": ":summary",
                "why_now": ":why_now",
                "opportunity_type": ":opportunity_type",
                "evidence_refs": ":evidence_refs",
                "confidence": ":confidence",
                "primary_group_id": ":group",
                "tags": ":tags",
                "risk": ":risk",
                "required_capabilities": ":capabilities",
                "proposed_actions_json": ":proposed_actions",
            }
            assignments = [
                "entity_type=if_not_exists(entity_type,:entity)",
                "user_id=if_not_exists(user_id,:user)",
                "candidate_id=if_not_exists(candidate_id,:candidate_id)",
                "source_type=if_not_exists(source_type,:source_type)",
                *[
                    f"{field}={value}"
                    if mail_profile_version is not None
                    else f"{field}=if_not_exists({field},{value})"
                    for field, value in proposal_fields.items()
                ],
                "fingerprint=if_not_exists(fingerprint,:fingerprint)",
                "#status=if_not_exists(#status,:visible)",
                "version=if_not_exists(version,:mail_zero)+:one"
                if mail_profile_version is not None
                else "version=if_not_exists(version,:one)",
                "created_at=if_not_exists(created_at,:created)",
                "updated_at=:created"
                if mail_profile_version is not None
                else "updated_at=if_not_exists(updated_at,:created)",
                "GSI1PK=:gsi_pk"
                if mail_profile_version is not None
                else "GSI1PK=if_not_exists(GSI1PK,:gsi_pk)",
                "GSI1SK=:gsi_sk"
                if mail_profile_version is not None
                else "GSI1SK=if_not_exists(GSI1SK,:gsi_sk)",
            ]
            if mail_profile_version is not None:
                assignments.extend(
                    [
                        "mail_profile_version=:mail_version",
                        "mail_scan_id=:mail_scan",
                        "mail_connection_id=:mail_epoch",
                    ]
                )
            transaction.append(
                {
                    "Update": {
                        "TableName": self._table_name,
                        "Key": _candidate_key(user_id, candidate_id),
                        "UpdateExpression": "SET " + ", ".join(assignments),
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": {
                            ":entity": {"S": "candidate"},
                            ":user": {"S": user_id},
                            ":candidate_id": {"S": candidate_id},
                            ":source_type": {"S": "CONNECTED_SIGNAL"},
                            ":outcome": {"S": str(candidate["outcome"])},
                            ":summary": {"S": str(candidate["summary"])},
                            ":why_now": {"S": str(candidate["why_now"])},
                            ":opportunity_type": {
                                "S": str(candidate["opportunity_type"])
                            },
                            ":evidence_refs": {
                                "L": [
                                    {"S": str(value)}
                                    for value in candidate["evidence_refs"]
                                ]
                            },
                            ":confidence": {"N": str(candidate["confidence"])},
                            ":group": {"S": group_id},
                            ":tags": {
                                "L": [
                                    {"S": str(value)}
                                    for value in candidate.get("tags", [])
                                ]
                            },
                            ":risk": {"S": str(candidate["risk"])},
                            ":capabilities": {
                                "L": [
                                    {"S": str(value)}
                                    for value in candidate.get(
                                        "required_capabilities", []
                                    )
                                ]
                            },
                            ":proposed_actions": {"S": proposed_actions},
                            ":fingerprint": {"S": fingerprint},
                            ":visible": {"S": "VISIBLE"},
                            ":one": {"N": "1"},
                            ":created": {"S": now},
                            ":gsi_pk": {
                                "S": f"USER#{user_id}#CANDIDATE#VISIBLE#{group_id}"
                            },
                            ":gsi_sk": {"S": f"{now}#{candidate_id}"},
                            **(
                                {
                                    ":mail_version": {"N": str(mail_profile_version)},
                                    ":mail_scan": {"S": mail_scan_id},
                                    ":mail_epoch": {"S": mail_connection_id or ""},
                                    ":mail_zero": {"N": "0"},
                                }
                                if mail_profile_version is not None
                                else {}
                            ),
                        },
                    }
                }
            )
            if candidate_guard:
                update = transaction[-1]["Update"]
                update["ConditionExpression"] = candidate_guard["ConditionExpression"]
                update["ExpressionAttributeNames"].update(
                    candidate_guard.get("ExpressionAttributeNames", {})
                )
                update["ExpressionAttributeValues"].update(
                    candidate_guard.get("ExpressionAttributeValues", {})
                )
        transaction.extend(suppression_guards.values())
        return transaction


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _scan_progress(processed: int, estimate: int, *, complete: bool) -> int:
    if complete:
        return 100
    if estimate <= 0:
        return 5
    return max(5, min(99, int(processed * 100 / estimate)))


def _candidate_fingerprint(candidate: Mapping[str, object]) -> str:
    canonical = json.dumps(
        candidate["fingerprint_inputs"],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _snapshot_guard(item: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    if not item:
        return {"ConditionExpression": "attribute_not_exists(PK)"}
    names, values, conditions = {}, {}, []
    for index, field in enumerate(fields):
        name, value = f"#observed{index}", f":observed{index}"
        names[name] = field
        if field in item:
            values[value] = item[field]
            conditions.append(f"{name}={value}")
        else:
            conditions.append(f"attribute_not_exists({name})")
    return {
        "ConditionExpression": " AND ".join(conditions),
        "ExpressionAttributeNames": names,
        **({"ExpressionAttributeValues": values} if values else {}),
    }


def _connection_key(user_id: str) -> dict[str, dict[str, str]]:
    return {"PK": {"S": f"USER#{user_id}"}, "SK": {"S": "CONNECTION#google"}}


def _evidence_key(user_id: str, evidence_ref: str) -> dict[str, dict[str, str]]:
    return {
        "PK": {"S": f"USER#{user_id}"},
        "SK": {"S": f"EVIDENCE#{evidence_ref}"},
    }


def _candidate_key(user_id: str, candidate_id: str) -> dict[str, dict[str, str]]:
    return {
        "PK": {"S": f"USER#{user_id}"},
        "SK": {"S": f"CANDIDATE#{candidate_id}"},
    }


def _dynamo_text(item: Mapping[str, Any], name: str) -> str | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("S"), str):
        return value["S"]
    return None
