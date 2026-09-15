"""Revision-bound mail jobs; no mailbox content is stored in queue messages."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .google_connection_store import (
    CandidateRefreshConflict,
    HistorySyncInProgress,
    _connection_key,
    _scan_progress,
)
from .google_jobs import (
    GMAIL_READONLY_SCOPE,
    MAX_INITIAL_SCAN_PAGES,
    _history_id,
    _page_token,
    _scan_details,
    _scan_id,
    _scan_page_details,
    _sync_details,
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> dict[str, str]:
    return {"S": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}


def _text(item: Mapping[str, Any], name: str) -> str:
    return item.get(name, {}).get("S", "")


def _key(user_id: str) -> dict[str, Any]:
    return {"PK": {"S": f"USER#{user_id}"}, "SK": {"S": "MAIL_INTERESTS#google"}}


def _configured(profile: Mapping[str, Any]) -> bool:
    return bool(profile.get("tags") or str(profile.get("description", "")).strip())


class StaleMailJob(Exception):
    """A newer profile, request or connection superseded this work."""


class MailAuthorizationRequired(StaleMailJob):
    """The current job is terminal until the user reconnects Google."""


class SqsMailQueue:
    def __init__(self, queue_url: str, client: Any) -> None:
        self.url = queue_url
        self.client = client

    def send(
        self, *, user_id: str, event_type: str, payload: Mapping[str, Any]
    ) -> None:
        canonical = json.dumps(
            [user_id, event_type, payload], sort_keys=True, separators=(",", ":")
        )
        envelope = {
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "user_id": user_id,
            "connector": "google",
            "occurred_at": _now(),
            "dedupe_key": f"google:mail:{hashlib.sha256(canonical.encode()).hexdigest()}",
            "trace_id": str(uuid.uuid4()),
            "payload": dict(payload),
        }
        self.client.send_message(
            QueueUrl=self.url, MessageBody=json.dumps(envelope, separators=(",", ":"))
        )


class DynamoMailJobStore:
    def __init__(self, table_name: str, client: Any) -> None:
        self.table = table_name
        self.client = client

    def load(
        self, user_id: str
    ) -> tuple[dict[str, Any], Mapping[str, Any], Mapping[str, Any]]:
        item = (
            self.client.get_item(
                TableName=self.table, Key=_key(user_id), ConsistentRead=True
            ).get("Item")
            or {}
        )
        connection = (
            self.client.get_item(
                TableName=self.table, Key=_connection_key(user_id), ConsistentRead=True
            ).get("Item")
            or {}
        )
        state = {
            "profile": {
                "tags": [],
                "description": "",
                "version": 0,
                "updated_at": None,
            },
            "recommendations": {
                "status": "NOT_STARTED",
                "request_id": None,
                "tags": [],
                "title_count": 0,
                "generated_at": None,
                "error_code": None,
            },
            "scan": {
                "status": "NOT_STARTED",
                "scan_id": None,
                "profile_version": 0,
                "processed_count": 0,
                "matched_count": 0,
                "completed_at": None,
                "error_code": None,
            },
        }
        for name in state:
            if _text(item, f"{name}_json"):
                state[name] = json.loads(_text(item, f"{name}_json"))
        return state, item, connection

    def guard(
        self, user_id: str, epoch: str, *, status: str = "CONNECTED"
    ) -> dict[str, Any]:
        values = {":status": {"S": status}}
        condition = "#status=:status AND attribute_not_exists(mail_connection_id)"
        if epoch:
            condition = "#status=:status AND mail_connection_id=:epoch"
            values[":epoch"] = {"S": epoch}
        return {
            "ConditionCheck": {
                "TableName": self.table,
                "Key": _connection_key(user_id),
                "ConditionExpression": condition,
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": values,
            }
        }

    def transact(self, updates: list[dict[str, Any]]) -> None:
        try:
            self.client.transact_write_items(TransactItems=updates)
        except self.client.exceptions.TransactionCanceledException as error:
            reasons = getattr(error, "response", {}).get("CancellationReasons", [])
            failed_keys = [
                _text(next(iter(updates[index].values())).get("Key", {}), "SK")
                for index, reason in enumerate(reasons)
                if reason.get("Code") == "ConditionalCheckFailed"
                and index < len(updates)
            ]
            if any(
                key in {"MAIL_INTERESTS#google", "CONNECTION#google"}
                for key in failed_keys
            ):
                raise StaleMailJob from error
            if failed_keys and all(
                key.startswith(("CANDIDATE#", "SUPPRESSION#")) for key in failed_keys
            ):
                raise CandidateRefreshConflict from error
            raise

    def setup(
        self,
        user_id: str,
        epoch: str,
        result: Mapping[str, Any],
        *,
        expected_status: str = "CONNECTING",
    ) -> None:
        if result.get("status") != "CONNECTED":
            raise ValueError("Mail setup did not connect Google")
        details = _scan_details({**result, "recent_message_estimate": 0})
        guard = self.guard(user_id, epoch, status=expected_status)["ConditionCheck"]
        values = guard["ExpressionAttributeValues"]
        now = _now()
        values.update(
            {
                ":connected": {"S": "CONNECTED"},
                ":user": {"S": user_id},
                ":history": {"S": details["history_id"]},
                ":expiration": {"N": details["watch_expiration"]},
                ":account": {"S": details["account_hash"]},
                ":now": {"S": now},
                ":scopes": {"L": [{"S": GMAIL_READONLY_SCOPE}]},
                ":zero": {"N": "0"},
                ":one": {"N": "1"},
                ":gsi1": {"S": f"GOOGLE_ACCOUNT#{details['account_hash']}"},
                ":gsi1sk": {"S": "CONNECTION#google"},
                ":gsi2": {"S": "CONNECTION#google#CONNECTED"},
                ":gsi2sk": {
                    "S": f"{details['watch_expiration']}#{hashlib.sha256(user_id.encode()).hexdigest()}"
                },
            }
        )
        guard["UpdateExpression"] = (
            "SET #status=:connected,user_id=:user,granted_scopes=:scopes,gmail_history_id=:history,"
            "watch_expiration=:expiration,account_hash=:account,watch_renewed_at=:now,last_checked_at=:now,"
            "scan_progress=:zero,discovery_revision=:zero,GSI1PK=:gsi1,GSI1SK=:gsi1sk,GSI2PK=:gsi2,GSI2SK=:gsi2sk,"
            "version=if_not_exists(version,:zero)+:one REMOVE gmail_scan_id,gmail_sync_token,gmail_sync_expires_at,error_code"
        )
        self.transact([{"Update": guard}])

    def setup_failed(
        self, user_id: str, epoch: str, *, expected_status: str = "CONNECTING"
    ) -> None:
        update = self.guard(user_id, epoch, status=expected_status)["ConditionCheck"]
        update["UpdateExpression"] = "SET #status=:error,error_code=:code"
        update["ExpressionAttributeValues"].update(
            {":error": {"S": "ERROR"}, ":code": {"S": "MAIL_SETUP_FAILED"}}
        )
        try:
            self.transact([{"Update": update}])
        except StaleMailJob:
            return

    def authorization_required(self, user_id: str, epoch: str) -> None:
        for _ in range(3):
            state, item, connection = self.load(user_id)
            if _text(connection, "mail_connection_id") != epoch or _text(
                connection, "status"
            ) not in {"CONNECTED", "CONNECTING", "ERROR"}:
                return
            update = self.guard(user_id, epoch, status=_text(connection, "status"))[
                "ConditionCheck"
            ]
            update["UpdateExpression"] = (
                "SET #status=:error,error_code=:code,last_checked_at=:now,version=if_not_exists(version,:zero)+:one REMOVE GSI1PK,GSI1SK,GSI2PK,GSI2SK,gmail_sync_token,gmail_sync_expires_at"
            )
            update["ExpressionAttributeValues"].update(
                {
                    ":error": {"S": "ERROR"},
                    ":code": {"S": "GOOGLE_AUTH_REQUIRED"},
                    ":now": {"S": _now()},
                    ":zero": {"N": "0"},
                    ":one": {"N": "1"},
                }
            )
            values = {
                ":recommendations": _json(
                    dict(
                        state["recommendations"],
                        status="ERROR",
                        error_code="GOOGLE_AUTH_REQUIRED",
                    )
                ),
                ":scan": _json(
                    dict(
                        state["scan"], status="ERROR", error_code="GOOGLE_AUTH_REQUIRED"
                    )
                ),
            }
            conditions = []
            for field in ("profile_version", "scan_id", "request_id"):
                if field in item:
                    conditions.append(f"{field}=:old_{field}")
                    values[f":old_{field}"] = item[field]
                else:
                    conditions.append(f"attribute_not_exists({field})")
            try:
                self.transact(
                    [
                        {"Update": update},
                        {
                            "Update": {
                                "TableName": self.table,
                                "Key": _key(user_id),
                                "UpdateExpression": "SET recommendations_json=:recommendations,scan_json=:scan",
                                "ConditionExpression": " AND ".join(conditions),
                                "ExpressionAttributeValues": values,
                            }
                        },
                    ]
                )
                return
            except StaleMailJob:
                continue
        # An actively edited profile must remain writable. The connection is
        # authoritative for both public auth-error projections even under contention.
        self.transact([{"Update": update}])

    def recover_setup(
        self,
        user_id: str,
        state: Mapping[str, Any],
        connection: Mapping[str, Any],
        *,
        kind: str = "scan",
        request_profile_version: int,
    ) -> str:
        old_epoch = _text(connection, "mail_connection_id")
        epoch = uuid.uuid4().hex
        update = self.guard(user_id, old_epoch, status=_text(connection, "status"))[
            "ConditionCheck"
        ]
        update["UpdateExpression"] = (
            "SET #status=:connecting,mail_connection_id=:new,gmail_scan_id=:new"
        )
        update["ExpressionAttributeValues"].update(
            {":connecting": {"S": "CONNECTING"}, ":new": {"S": epoch}}
        )
        identity_field = "scan_id" if kind == "scan" else "request_id"
        condition = f"{identity_field}=:id AND {kind}_connection_id=:old"
        values = {
            ":id": {"S": state[kind][identity_field]},
            ":old": {"S": old_epoch},
            ":new": {"S": epoch},
            ":recovery": _json(
                {
                    "kind": kind,
                    "request_id": state[kind][identity_field],
                    "profile_version": request_profile_version,
                    "connection_id": epoch,
                }
            ),
        }
        if kind == "scan":
            condition += " AND profile_version=:version"
            values[":version"] = {"N": str(state["profile"]["version"])}
        self.transact(
            [
                {"Update": update},
                {
                    "Update": {
                        "TableName": self.table,
                        "Key": _key(user_id),
                        "UpdateExpression": f"SET {kind}_connection_id=:new,mail_recovery_json=:recovery",
                        "ConditionExpression": condition,
                        "ExpressionAttributeValues": values,
                    }
                },
            ]
        )
        return epoch

    def revoke_complete(self, user_id: str, request_id: str | None) -> None:
        condition = "#status=:revoking AND attribute_not_exists(gmail_scan_id)"
        values = {
            ":revoking": {"S": "REVOKING"},
            ":disconnected": {"S": "DISCONNECTED"},
            ":empty": {"L": []},
            ":zero": {"N": "0"},
            ":one": {"N": "1"},
            ":now": {"S": _now()},
        }
        if request_id is not None:
            condition = "#status=:revoking AND gmail_scan_id=:request"
            values[":request"] = {"S": request_id}
        self.transact(
            [
                {
                    "Update": {
                        "TableName": self.table,
                        "Key": _connection_key(user_id),
                        "ConditionExpression": condition,
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": values,
                        "UpdateExpression": "SET #status=:disconnected,granted_scopes=:empty,scan_progress=:zero,discovery_revision=:zero,last_checked_at=:now,version=if_not_exists(version,:zero)+:one REMOVE gmail_scan_id,mail_connection_id,gmail_history_id,gmail_sync_token,gmail_sync_expires_at,watch_expiration,watch_renewed_at,account_hash,GSI1PK,GSI1SK,GSI2PK,GSI2SK,error_code",
                    }
                }
            ]
        )

    def ensure_job(self, user_id: str, kind: str) -> Mapping[str, Any] | None:
        state, item, connection = self.load(user_id)
        if _text(connection, "status") != "CONNECTED":
            return None
        if kind == "scan" and not _configured(state["profile"]):
            return None
        epoch = _text(connection, "mail_connection_id")
        field = "scan_id" if kind == "scan" else "request_id"
        old = state[kind]
        same_epoch = _text(item, f"{kind}_connection_id") == epoch
        if same_epoch and old["status"] in {"PENDING", "READY"}:
            return (
                {
                    "request_id": old[field],
                    "profile_version": state["profile"]["version"],
                }
                if old["status"] == "PENDING"
                else None
            )
        identity = uuid.uuid4().hex
        record = dict(old, status="PENDING", error_code=None)
        record[field] = identity
        if kind == "scan":
            record.update(
                profile_version=state["profile"]["version"],
                processed_count=0,
                matched_count=0,
                completed_at=None,
            )
        else:
            record.update(tags=[], title_count=0, generated_at=None)
        values = {
            ":record": _json(record),
            ":id": {"S": identity},
            ":epoch": {"S": epoch},
            ":old": {"S": old[field] or ""},
        }
        condition = f"(attribute_not_exists({field}) OR {field}=:old)"
        if kind == "scan":
            condition += " AND profile_version=:version"
            values[":version"] = {"N": str(state["profile"]["version"])}
        expression = f"SET {kind}_json=:record,{field}=:id,{kind}_connection_id=:epoch"
        if kind == "scan":
            expression += ",scan_step=:zero,scan_next_json=:null"
            values.update({":zero": {"N": "0"}, ":null": _json(None)})
        self.transact(
            [
                {
                    "Update": {
                        "TableName": self.table,
                        "Key": _key(user_id),
                        "UpdateExpression": expression,
                        "ConditionExpression": condition,
                        "ExpressionAttributeValues": values,
                    }
                },
                self.guard(user_id, epoch),
            ]
        )
        return {"request_id": identity, "profile_version": state["profile"]["version"]}

    def finish_recommendations(
        self, user_id: str, identity: str, epoch: str, result: Mapping[str, Any]
    ) -> None:
        tags = result.get("tags")
        count = result.get("title_count")
        if (
            result.get("status") != "INTEREST_TAGS"
            or not isinstance(tags, list)
            or len(tags) > 8
            or type(count) is not int
            or not 0 <= count <= 32
        ):
            raise ValueError("Mail recommendation response is invalid")
        for tag in tags:
            if (
                not isinstance(tag, Mapping)
                or set(tag) != {"tag", "evidence_refs"}
                or not isinstance(tag["tag"], str)
                or not 1 <= len(tag["tag"]) <= 32
                or not isinstance(tag["evidence_refs"], list)
                or not tag["evidence_refs"]
                or any(not isinstance(ref, str) for ref in tag["evidence_refs"])
            ):
                raise ValueError("Mail recommendation tag is invalid")
            if (
                tag["tag"] != unicodedata.normalize("NFKC", tag["tag"]).strip()
                or "#" in tag["tag"]
                or any(c.isspace() for c in tag["tag"])
            ):
                raise ValueError("Mail recommendation tag is not canonical")
        if len({tag["tag"].casefold() for tag in tags}) != len(tags):
            raise ValueError("Mail recommendation tags are duplicated")
        generated = result.get("generated_at")
        if not isinstance(generated, str):
            raise TypeError("Mail recommendation timestamp is invalid")
        record = {
            "status": "READY",
            "request_id": identity,
            "tags": tags,
            "title_count": count,
            "generated_at": generated,
            "error_code": None,
        }
        self.transact(
            [
                {
                    "Update": {
                        "TableName": self.table,
                        "Key": _key(user_id),
                        "UpdateExpression": "SET recommendations_json=:record",
                        "ConditionExpression": "request_id=:id AND recommendations_connection_id=:epoch",
                        "ExpressionAttributeValues": {
                            ":record": _json(record),
                            ":id": {"S": identity},
                            ":epoch": {"S": epoch},
                        },
                    }
                },
                self.guard(user_id, epoch),
            ]
        )

    def fail(self, user_id: str, kind: str, identity: str, epoch: str) -> None:
        state, _, _ = self.load(user_id)
        field = "scan_id" if kind == "scan" else "request_id"
        if state[kind][field] != identity:
            return
        record = dict(state[kind], status="ERROR", error_code="MAIL_ANALYSIS_FAILED")
        try:
            self.transact(
                [
                    {
                        "Update": {
                            "TableName": self.table,
                            "Key": _key(user_id),
                            "UpdateExpression": f"SET {kind}_json=:record",
                            "ConditionExpression": f"{field}=:id AND {kind}_connection_id=:epoch",
                            "ExpressionAttributeValues": {
                                ":record": _json(record),
                                ":id": {"S": identity},
                                ":epoch": {"S": epoch},
                            },
                        }
                    },
                    self.guard(user_id, epoch),
                ]
            )
        except StaleMailJob:
            return

    def persist(
        self,
        user_id: str,
        state: Mapping[str, Any],
        item: Mapping[str, Any],
        epoch: str,
        result: Mapping[str, Any],
        page: Mapping[str, Any],
        writer: Any,
        *,
        step: int,
        next_work: Mapping[str, Any] | None,
        history_claim: Any = None,
    ) -> None:
        version = state["profile"]["version"]
        matches = _matches(result, version, page["evidence"])
        if any(
            not set(match["matched_tags"]).issubset(state["profile"]["tags"])
            for match in matches
        ):
            raise ValueError("Mail matched tags do not match saved interests")
        scan = state["scan"]
        scan_id = scan["scan_id"]
        matched_refs = {match["evidence_ref"] for match in matches}
        evidence = [
            record for record in page["evidence"] if record["ref"] in matched_refs
        ]
        if any(
            not set(candidate["evidence_refs"]).issubset(matched_refs)
            for candidate in page["candidates"]
        ):
            raise ValueError("Mail candidates contain unmatched evidence")
        signal_time = _now()
        transactions = writer._signal_updates(
            user_id,
            evidence=evidence,
            candidates=page["candidates"],
            now=signal_time,
            mail_profile_version=version,
            mail_scan_id=scan_id,
            mail_connection_id=epoch,
        )
        signal_update_count = len(transactions)
        new_match_count = 0
        for match in matches:
            suffix = hashlib.sha256(match["evidence_ref"].encode()).hexdigest()
            result_key = {
                "PK": {"S": f"USER#{user_id}"},
                "SK": {"S": f"MAIL_RESULT#{scan_id}#{suffix}"},
            }
            existing = self.client.get_item(
                TableName=self.table,
                Key=result_key,
                ConsistentRead=True,
                ProjectionExpression="PK",
            ).get("Item")
            if not existing:
                new_match_count += 1
            transactions.append(
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": {
                            **result_key,
                            "result_json": _json(match),
                            "profile_version": {"N": str(version)},
                        },
                    }
                }
            )
        processed = page.get("processed_message_count", len(page["evidence"]))
        complete = history_claim is not None or page.get("next_page_token") is None
        action_incomplete = (
            bool(page.get("action_preparation_incomplete"))
            or (scan.get("error_code") == "MAIL_ACTION_PREPARATION_INCOMPLETE")
            or _text(item, "scan_action_warning_id") == scan_id
        )
        record = dict(
            scan,
            status="READY" if complete else "PENDING",
            processed_count=scan["processed_count"] + processed,
            matched_count=scan["matched_count"] + new_match_count,
            completed_at=_now() if complete else None,
            error_code="MAIL_ACTION_PREPARATION_INCOMPLETE"
            if action_incomplete
            else None,
        )
        values = {
            ":record": _json(record),
            ":id": {"S": scan_id},
            ":version": {"N": str(version)},
            ":epoch": {"S": epoch},
            ":next": _json(next_work),
            ":step": {"N": str(step)},
            ":new_step": {"N": str(step + 1)},
        }
        condition = (
            "profile_version=:version AND scan_id=:id AND scan_connection_id=:epoch"
        )
        condition += (
            " AND (attribute_not_exists(scan_step) OR scan_step=:step)"
            if step == 0
            else " AND scan_step=:step"
        )
        update_expression = (
            "SET scan_json=:record,scan_step=:new_step,scan_next_json=:next"
        )
        if action_incomplete:
            # Keep this scan's warning through a later transient analysis error.
            # A new scan has a different identity and does not inherit it.
            update_expression += ",scan_action_warning_id=:id"
        transactions.append(
            {
                "Update": {
                    "TableName": self.table,
                    "Key": _key(user_id),
                    "UpdateExpression": update_expression,
                    "ConditionExpression": condition,
                    "ExpressionAttributeValues": values,
                }
            }
        )
        connection_update = self.guard(user_id, epoch)["ConditionCheck"]
        if history_claim is not None:
            connection_update["ConditionExpression"] += (
                " AND gmail_history_id=:start AND gmail_sync_token=:token"
            )
            connection_update["ExpressionAttributeValues"].update(
                {
                    ":start": {"S": history_claim.start_history_id},
                    ":token": {"S": history_claim.token},
                }
            )
            history = page["history_id"]
        else:
            history = result.get("history_id")
        progress = (
            100
            if history_claim is not None
            else _scan_progress(
                record["processed_count"],
                page["recent_message_estimate"],
                complete=complete,
            )
        )
        connection_update["UpdateExpression"] = (
            "SET last_checked_at=:now,scan_progress=:progress,discovery_revision=:revision,last_sync_mode=:mode"
        )
        connection_update["ExpressionAttributeValues"].update(
            {
                ":now": {"S": _now()},
                ":progress": {"N": str(progress)},
                ":revision": {"N": "1" if complete and not action_incomplete else "0"},
                ":mode": {"S": page.get("recovery_mode", "INITIAL_7_DAY")},
            }
        )
        if history is not None:
            connection_update["UpdateExpression"] += ",gmail_history_id=:history"
            connection_update["ExpressionAttributeValues"].update(
                {":history": {"S": _history_id(history)}}
            )
        if result.get("watch_expiration") is not None:
            details = _scan_details(result)
            connection_update["UpdateExpression"] += (
                ",watch_expiration=:expiration,watch_renewed_at=:now,GSI2SK=:gsi2sk"
            )
            connection_update["ExpressionAttributeValues"].update(
                {
                    ":expiration": {"N": details["watch_expiration"]},
                    ":gsi2sk": {
                        "S": f"{details['watch_expiration']}#{hashlib.sha256(user_id.encode()).hexdigest()}"
                    },
                }
            )
        if history is not None:
            connection_update["UpdateExpression"] += (
                " REMOVE gmail_sync_token,gmail_sync_expires_at"
            )
        transactions.append({"Update": connection_update})
        page_updates = transactions[signal_update_count:]
        for attempt in range(3):
            try:
                self.transact(transactions)
                return
            except CandidateRefreshConflict:
                if attempt == 2:
                    raise
                # Re-read feedback and rebuild only the proposal writes. Keep the
                # original mail/connection guards and never repeat model inference.
                transactions = (
                    writer._signal_updates(
                        user_id,
                        evidence=evidence,
                        candidates=page["candidates"],
                        now=signal_time,
                        mail_profile_version=version,
                        mail_scan_id=scan_id,
                        mail_connection_id=epoch,
                    )
                    + page_updates
                )


def _matches(
    result: Mapping[str, Any], version: int, evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    matches = result.get("interest_matches")
    if (
        result.get("interest_validated") is not True
        or type(result.get("interest_profile_revision")) is not int
        or result.get("interest_profile_revision") != version
        or not isinstance(matches, list)
        or len(matches) > 8
    ):
        raise ValueError("Mail interest response is invalid")
    refs = {record["ref"]: record for record in evidence}
    seen: set[str] = set()
    expected = {
        "evidence_ref",
        "title",
        "summary",
        "reason",
        "sender_domain",
        "received_at",
        "matched_tags",
    }
    for value in matches:
        if (
            not isinstance(value, dict)
            or not expected.issubset(value)
            or not set(value).issubset(expected | {"importance"})
            or value.get("evidence_ref") not in refs
            or value["evidence_ref"] in seen
        ):
            raise ValueError("Mail result references are invalid")
        seen.add(value["evidence_ref"])
        importance = value.get("importance", "NORMAL")
        if not isinstance(importance, str) or importance not in {
            "HIGH",
            "NORMAL",
            "LOW",
        }:
            raise ValueError("Mail result importance is invalid")
        for key, maximum in (
            ("title", 512),
            ("summary", 1000),
            ("reason", 1000),
            ("sender_domain", 255),
        ):
            if not isinstance(value[key], str) or len(value[key]) > maximum:
                raise ValueError("Mail result text is invalid")
        if value["title"] != refs[value["evidence_ref"]]["title"]:
            raise ValueError("Mail result title is not grounded")
        if value["received_at"] is not None and not isinstance(
            value["received_at"], str
        ):
            raise ValueError("Mail result timestamp is invalid")
        if (
            not isinstance(value["matched_tags"], list)
            or len(value["matched_tags"]) > 8
            or any(
                not isinstance(tag, str) or len(tag) > 32
                for tag in value["matched_tags"]
            )
        ):
            raise ValueError("Mail result tags are invalid")
    return matches


class MailJobProcessor:
    def __init__(
        self,
        runtime: Any,
        writer: Any,
        store: DynamoMailJobStore,
        queue: Any,
        legacy: Any,
    ) -> None:
        self.runtime, self.writer, self.store, self.queue, self.legacy = (
            runtime,
            writer,
            store,
            queue,
            legacy,
        )

    def _invoke(
        self,
        user_id: str,
        operation: str,
        epoch: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = (
            self.runtime.invoke(user_id, operation)
            if parameters is None
            else self.runtime.invoke(user_id, operation, parameters)
        )
        if result == {
            "status": "AUTHORIZATION_REQUIRED",
            "error_code": "GOOGLE_AUTH_REQUIRED",
        }:
            self.store.authorization_required(user_id, epoch)
            raise MailAuthorizationRequired
        return result

    def process(self, envelope: Mapping[str, Any]) -> None:
        if envelope.get("connector") != "google":
            raise ValueError("Mail job connector is invalid")
        user_id = envelope.get("user_id")
        if not isinstance(user_id, str) or not user_id or len(user_id) > 128:
            raise ValueError("Mail job owner is invalid")
        event = envelope.get("event_type")
        if event in {"INITIAL_SCAN_REQUESTED", "INITIAL_SCAN_CONTINUATION"}:
            return  # Superseded broad-discovery jobs may still be in flight.
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            raise TypeError("Mail job payload is invalid")
        state, item, connection = self.store.load(user_id)
        epoch = _text(connection, "mail_connection_id")
        if event == "GOOGLE_CONNECTION_REVOKED":
            request_id = payload.get("request_id")
            if request_id is not None:
                request_id = _scan_id(request_id)
            if _text(connection, "status") != "REVOKING" or _text(
                connection, "gmail_scan_id"
            ) != (request_id or ""):
                return
            result = self.runtime.invoke(user_id, "GOOGLE_DISCONNECT")
            if result.get("status") != "DISCONNECTED":
                raise ValueError("Google disconnection did not complete")
            try:
                self.store.revoke_complete(user_id, request_id)
            except StaleMailJob:
                return
            return
        if (
            _text(connection, "status") == "ERROR"
            and _text(connection, "error_code") == "GOOGLE_AUTH_REQUIRED"
        ):
            return
        if event == "MAIL_SETUP_REQUESTED":
            requested = _scan_id(payload.get("scan_id"))
            if epoch != requested or _text(connection, "status") not in {
                "CONNECTING",
                "CONNECTED",
                "ERROR",
            }:
                return
            try:
                if _text(connection, "status") != "CONNECTED":
                    self.store.setup(
                        user_id,
                        epoch,
                        self._invoke(user_id, "GOOGLE_MAIL_SETUP", epoch),
                        expected_status=_text(connection, "status"),
                    )
                for kind, followup in (
                    ("recommendations", "MAIL_RECOMMENDATIONS_REQUESTED"),
                    ("scan", "MAIL_SCAN_REQUESTED"),
                ):
                    work = self.store.ensure_job(user_id, kind)
                    if work is not None:
                        try:
                            self.queue.send(
                                user_id=user_id, event_type=followup, payload=work
                            )
                        except Exception:
                            self.store.fail(user_id, kind, work["request_id"], epoch)
                            raise
            except StaleMailJob:
                return
            except Exception:
                if _text(connection, "status") != "CONNECTED":
                    self.store.setup_failed(
                        user_id, epoch, expected_status=_text(connection, "status")
                    )
                raise
            return
        if event in {"MAIL_SCAN_REQUESTED", "MAIL_RECOMMENDATIONS_REQUESTED"} and _text(
            connection, "status"
        ) in {
            "SCANNING",
            "ERROR",
            "CONNECTING",
            "CONNECTED",
        }:
            scopes = connection.get("granted_scopes", {}).get("L", [])
            kind = "scan" if event == "MAIL_SCAN_REQUESTED" else "recommendations"
            identity_field = "scan_id" if kind == "scan" else "request_id"
            status = _text(connection, "status")
            recovery = json.loads(_text(item, "mail_recovery_json") or "null")
            resuming = type(payload.get("profile_version")) is int and recovery == {
                "kind": kind,
                "request_id": payload.get("request_id"),
                "profile_version": payload["profile_version"],
                "connection_id": epoch,
            }
            if status != "CONNECTED" or resuming:
                if (
                    {"S": GMAIL_READONLY_SCOPE} not in scopes
                    or _text(item, f"{kind}_connection_id") != epoch
                    or type(payload.get("profile_version")) is not int
                    or payload["profile_version"] < 0
                    or (
                        not resuming
                        and (
                            status == "CONNECTING"
                            or payload.get("request_id") != state[kind][identity_field]
                            or (
                                kind == "scan"
                                and (
                                    not _configured(state["profile"])
                                    or payload["profile_version"]
                                    != state["profile"]["version"]
                                )
                            )
                        )
                    )
                ):
                    return
                try:
                    if not resuming:
                        epoch = self.store.recover_setup(
                            user_id,
                            state,
                            connection,
                            kind=kind,
                            request_profile_version=payload["profile_version"],
                        )
                        status = "CONNECTING"
                    if status != "CONNECTED":
                        self.store.setup(
                            user_id,
                            epoch,
                            self._invoke(user_id, "GOOGLE_MAIL_SETUP", epoch),
                            expected_status=status,
                        )
                except StaleMailJob:
                    return
                except Exception:
                    self.store.setup_failed(user_id, epoch, expected_status=status)
                    raise
                self.process(
                    {
                        "connector": "google",
                        "user_id": user_id,
                        "event_type": "MAIL_SETUP_REQUESTED",
                        "payload": {"scan_id": epoch},
                    }
                )
                # The followup can share this event's dedupe key. Process the
                # original operation before its outer idempotency lease completes.
                state, item, connection = self.store.load(user_id)
                epoch = _text(connection, "mail_connection_id")
        if _text(connection, "status") != "CONNECTED":
            return
        if event == "MAIL_RECOMMENDATIONS_REQUESTED":
            identity = _scan_id(payload.get("request_id"))
            if (
                state["recommendations"]["request_id"] != identity
                or _text(item, "recommendations_connection_id") != epoch
                or state["recommendations"]["status"] == "READY"
            ):
                return
            try:
                result = self._invoke(user_id, "GOOGLE_INTEREST_TAGS", epoch)
                self.store.finish_recommendations(user_id, identity, epoch, result)
            except StaleMailJob:
                return
            except Exception:
                self.store.fail(user_id, "recommendations", identity, epoch)
                raise
            return
        if event not in {
            "MAIL_SCAN_REQUESTED",
            "MAIL_SCAN_CONTINUATION",
            "GMAIL_HISTORY_AVAILABLE",
        }:
            raise ValueError("Mail job event is unsupported")
        if (
            not _configured(state["profile"])
            or _text(item, "scan_connection_id") != epoch
        ):
            return
        scan = state["scan"]
        version = state["profile"]["version"]
        profile = {
            "revision": version,
            "tags": state["profile"]["tags"],
            "description": state["profile"]["description"],
        }
        if scan["scan_id"] is None or scan["profile_version"] != version:
            return
        current_step = int(item.get("scan_step", {}).get("N", "0"))
        claim = None
        persisted = False
        try:
            if event == "GMAIL_HISTORY_AVAILABLE":
                if scan["status"] != "READY" and not (
                    scan["status"] == "ERROR" and scan["completed_at"] is not None
                ):
                    return
                notified = _history_id(payload.get("history_id"))
                claim = self.writer.claim_history_sync(
                    user_id, notified_history_id=notified
                )
                if claim is None:
                    return
                result = self._invoke(
                    user_id,
                    "GOOGLE_INTEREST_HISTORY_SYNC",
                    epoch,
                    {
                        "start_history_id": claim.start_history_id,
                        "interest_profile": profile,
                    },
                )
                page = _sync_details(result, interest_results=True)
                next_work = (
                    {
                        "event_type": "GMAIL_HISTORY_AVAILABLE",
                        "payload": {
                            "history_id": notified,
                            "after_history_id": page["history_id"],
                        },
                    }
                    if page["continuation_required"]
                    else None
                )
                step = current_step
            else:
                identity = _scan_id(payload.get("request_id"))
                if (
                    identity != scan["scan_id"]
                    or payload.get("profile_version") != version
                ):
                    return
                step = payload.get("step", 0)
                if type(step) is not int or not 0 <= step < MAX_INITIAL_SCAN_PAGES:
                    raise ValueError("Mail scan page bound exceeded")
                if current_step != step:
                    next_work = json.loads(_text(item, "scan_next_json") or "null")
                    if current_step > step and next_work is not None:
                        self.queue.send(user_id=user_id, **next_work)
                    return
                parameters: dict[str, Any] = {"interest_profile": profile}
                if event == "MAIL_SCAN_CONTINUATION":
                    parameters["page_token"] = _page_token(payload.get("page_token"))
                result = self._invoke(
                    user_id,
                    "GOOGLE_INTEREST_SCAN_PAGE"
                    if event == "MAIL_SCAN_CONTINUATION"
                    else "GOOGLE_INTEREST_SCAN",
                    epoch,
                    parameters,
                )
                page = _scan_page_details(result, interest_results=True)
                next_token = page["next_page_token"]
                if next_token is not None:
                    if step + 1 >= MAX_INITIAL_SCAN_PAGES:
                        raise ValueError("Mail scan page bound exceeded")
                    hashes = payload.get("page_token_hashes", [])
                    if not isinstance(hashes, list) or any(
                        not isinstance(h, str) for h in hashes
                    ):
                        raise ValueError("Mail page token lineage is invalid")
                    token_hash = hashlib.sha256(next_token.encode()).hexdigest()
                    if token_hash in hashes:
                        raise ValueError("Mail page token cycle")
                    next_work = {
                        "event_type": "MAIL_SCAN_CONTINUATION",
                        "payload": {
                            "request_id": identity,
                            "profile_version": version,
                            "step": step + 1,
                            "page_token": next_token,
                            "page_token_hashes": [*hashes, token_hash],
                        },
                    }
                else:
                    next_work = {
                        "event_type": "GMAIL_HISTORY_AVAILABLE",
                        "payload": {"history_id": page["completion_history_id"]},
                    }
            self.store.persist(
                user_id,
                state,
                item,
                epoch,
                result,
                page,
                self.writer,
                step=step,
                next_work=next_work,
                history_claim=claim,
            )
            persisted = True
            if next_work is not None:
                self.queue.send(user_id=user_id, **next_work)
        except StaleMailJob:
            if claim is not None:
                self.writer.release_history_sync(user_id, token=claim.token)
            return
        except HistorySyncInProgress:
            raise
        except Exception:
            if claim is not None:
                self.writer.release_history_sync(user_id, token=claim.token)
            if not persisted:
                self.store.fail(user_id, "scan", scan["scan_id"], epoch)
            raise
