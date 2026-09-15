"""Owner-scoped mail preferences and asynchronous mail work."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

MAX_MAIL_RESULTS = 512
MAIL_RESULTS_PAGE_SIZE = 30
MAIL_RESULT_ORDER = "importance-v1"
MAIL_IMPORTANCE = {"HIGH": 0, "NORMAL": 1, "LOW": 2}
MAX_MAIL_SCAN_PAGES = 64  # Matches the Worker's bounded initial scan protocol.


def _result_order(item: Mapping[str, Any]) -> tuple[int, float, str]:
    importance = item.get("importance", "NORMAL")
    if not isinstance(importance, str) or importance not in MAIL_IMPORTANCE:
        raise ValueError("Mail result importance is invalid")
    reference = item.get("evidence_ref")
    if not isinstance(reference, str) or not reference:
        raise ValueError("Mail result reference is invalid")
    received = item.get("received_at")
    timestamp = float("-inf")
    if received is not None:
        if not isinstance(received, str):
            raise ValueError("Mail result timestamp is invalid")
        try:
            date = datetime.fromisoformat(received)
            if date.tzinfo is None:
                raise ValueError
            timestamp = date.timestamp()
        except (ValueError, OverflowError, OSError):
            raise ValueError("Mail result timestamp is invalid") from None
    return MAIL_IMPORTANCE[importance], -timestamp, reference


class MailInputError(ValueError):
    pass


class MailConflict(RuntimeError):
    pass


def empty_state() -> dict[str, Any]:
    return {
        "profile": {"tags": [], "description": "", "version": 0, "updated_at": None},
        "recommendations": {
            "status": "NOT_STARTED",
            "request_id": None,
            "tags": [],
            "title_count": 0,
            "generated_at": None,
            "error_code": None,
        },
        "scan": empty_scan(0),
    }


def empty_scan(version: int) -> dict[str, Any]:
    return {
        "status": "NOT_STARTED",
        "scan_id": None,
        "profile_version": version,
        "processed_count": 0,
        "matched_count": 0,
        "completed_at": None,
        "error_code": None,
    }


def configured(profile: Mapping[str, Any]) -> bool:
    return bool(profile.get("tags") or str(profile.get("description", "")).strip())


def _scan_eligible(connection: Mapping[str, Any]) -> bool:
    if _auth_required(connection):
        return False
    status = _text(connection, "status")
    scopes = connection.get("granted_scopes", {}).get("L", [])
    return status == "CONNECTED" or (
        status in {"SCANNING", "ERROR"}
        and {"S": "https://www.googleapis.com/auth/gmail.readonly"} in scopes
    )


def _auth_required(connection: Mapping[str, Any]) -> bool:
    return (
        _text(connection, "status") == "ERROR"
        and _text(connection, "error_code") == "GOOGLE_AUTH_REQUIRED"
    )


def _setup_pending(connection: Mapping[str, Any]) -> bool:
    return (
        _text(connection, "status") == "CONNECTING"
        and bool(_text(connection, "mail_connection_id"))
        and {"S": "https://www.googleapis.com/auth/gmail.readonly"}
        in connection.get("granted_scopes", {}).get("L", [])
    )


def profile_input(
    tags: object, description: object, version: object
) -> tuple[list[str], str, int]:
    if type(version) is not int or version < 0:
        raise MailInputError("expected_version is invalid")
    if not isinstance(tags, list) or len(tags) > 8:
        raise MailInputError("tags is invalid")
    normalized: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            raise MailInputError("tag is invalid")
        value = unicodedata.normalize("NFKC", tag).strip()
        if (
            not value
            or len(value) > 32
            or "#" in value
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise MailInputError("tag is invalid")
        if value.casefold() not in {item.casefold() for item in normalized}:
            normalized.append(value)
    if not isinstance(description, str) or len(description) > 1000:
        raise MailInputError("description is invalid")
    return normalized, description, version


def _key(user_id: str) -> dict[str, Any]:
    return {"PK": {"S": f"USER#{user_id}"}, "SK": {"S": "MAIL_INTERESTS#google"}}


def _json(value: object) -> dict[str, str]:
    return {"S": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}


def _text(item: Mapping[str, Any], name: str) -> str | None:
    value = item.get(name)
    return value.get("S") if isinstance(value, Mapping) else None


def _state(item: Mapping[str, Any]) -> dict[str, Any]:
    state = empty_state()
    for name in state:
        raw = _text(item, f"{name}_json")
        if raw is not None:
            state[name] = json.loads(raw)
    return state


def _pending_request(
    state: Mapping[str, Any],
    item: Mapping[str, Any],
    connection: Mapping[str, Any],
    kind: str,
) -> bool:
    record = state[kind]
    field = "scan_id" if kind == "scan" else "request_id"
    return (
        _text(connection, "status") == "CONNECTED"
        and record["status"] == "PENDING"
        and bool(record[field])
        and _text(item, field) == record[field]
        and _text(item, f"{kind}_connection_id")
        == (_text(connection, "mail_connection_id") or "")
        and (
            kind != "scan"
            or (
                configured(state["profile"])
                and record["profile_version"] == state["profile"]["version"]
            )
        )
    )


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _scan_step(item: Mapping[str, Any]) -> int:
    value = item.get("scan_step", {"N": "0"})
    raw = value.get("N") if isinstance(value, Mapping) else None
    if not isinstance(raw, str) or not re.fullmatch(r"0|[1-9][0-9]?", raw):
        raise MailConflict("Mail scan continuation is invalid")
    step = int(raw)
    if step >= MAX_MAIL_SCAN_PAGES:
        raise MailConflict("Mail scan continuation is invalid")
    return step


def _scan_continuation(
    state: Mapping[str, Any], item: Mapping[str, Any], connection: Mapping[str, Any]
) -> dict[str, Any]:
    scan, profile = state["scan"], state["profile"]
    step = _scan_step(item)
    epoch = _text(connection, "mail_connection_id")
    try:
        work = json.loads(_text(item, "scan_next_json") or "null")
        payload = work["payload"]
        token = payload["page_token"]
        hashes = payload["page_token_hashes"]
        valid = (
            isinstance(work, dict)
            and set(work) == {"event_type", "payload"}
            and work["event_type"] == "MAIL_SCAN_CONTINUATION"
            and isinstance(payload, dict)
            and set(payload)
            == {
                "request_id",
                "profile_version",
                "step",
                "page_token",
                "page_token_hashes",
            }
            and scan["status"] in {"PENDING", "ERROR"}
            and scan["completed_at"] is None
            and configured(profile)
            and type(profile["version"]) is int
            and type(scan["profile_version"]) is int
            and type(payload["profile_version"]) is int
            and payload["profile_version"]
            == scan["profile_version"]
            == profile["version"]
            and item.get("profile_version") == {"N": str(profile["version"])}
            and isinstance(payload["request_id"], str)
            and re.fullmatch(r"[0-9a-f]{32}", payload["request_id"]) is not None
            and payload["request_id"] == scan["scan_id"] == _text(item, "scan_id")
            and _text(connection, "status") == "CONNECTED"
            and bool(epoch)
            and _text(item, "scan_connection_id") == epoch
            and type(payload["step"]) is int
            and payload["step"] == step
            and 0 < step < MAX_MAIL_SCAN_PAGES
            and isinstance(token, str)
            and 0 < len(token) <= 2048
            and all(char.isprintable() and not char.isspace() for char in token)
            and isinstance(hashes, list)
            and len(hashes) == step
            and all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for value in hashes
            )
            and len(set(hashes)) == len(hashes)
            and hashes[-1] == hashlib.sha256(token.encode()).hexdigest()
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise MailConflict("Mail scan continuation is invalid")
    return work


def _scan_snapshot(item: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    conditions, values = [], {}
    for field in (
        "profile_version",
        "scan_id",
        "scan_connection_id",
        "scan_json",
        "scan_step",
        "scan_next_json",
    ):
        if field in item:
            slot = f":snapshot_{field}"
            conditions.append(f"{field}={slot}")
            values[slot] = item[field]
        else:
            conditions.append(f"attribute_not_exists({field})")
    return " AND ".join(conditions), values


@dataclass(frozen=True)
class _ScanWork:
    event_type: str
    payload: dict[str, Any]
    item: Mapping[str, Any]
    connection: Mapping[str, Any]


class MailInterestService:
    def __init__(self, store: Any, queue: Any) -> None:
        self.store = store
        self.queue = queue

    def state(self, user_id: str) -> dict[str, Any]:
        return self.store.public_state(user_id)

    def save(
        self,
        user_id: str,
        *,
        tags: object,
        description: object,
        expected_version: object,
    ) -> dict[str, Any]:
        values = profile_input(tags, description, expected_version)
        state, dispatch_scan = self.store.save_profile(user_id, *values)
        if dispatch_scan and state["scan"]["status"] == "PENDING":
            self._send(user_id, "MAIL_SCAN_REQUESTED", state, "scan")
        return self.state(user_id)

    def recommend(self, user_id: str) -> dict[str, Any]:
        state, dispatch = self.store.request(user_id, "recommendations")
        if dispatch:
            self._send(
                user_id, "MAIL_RECOMMENDATIONS_REQUESTED", state, "recommendations"
            )
        return self.state(user_id)

    def scan(self, user_id: str) -> dict[str, Any]:
        state, dispatch = self.store.request(user_id, "scan")
        if dispatch:
            self._send(user_id, "MAIL_SCAN_REQUESTED", state, "scan")
        return self.state(user_id)

    def results(self, user_id: str, cursor: object = None) -> dict[str, Any]:
        return self.store.results(user_id, cursor)

    def _send(
        self, user_id: str, event_type: str, state: Mapping[str, Any], kind: str
    ) -> None:
        record = state[kind]
        identity = record["scan_id" if kind == "scan" else "request_id"]
        payload = {
            "request_id": identity,
            "profile_version": state["profile"]["version"],
        }
        work = None
        if kind == "scan":
            work = self.store.scan_work(user_id, identity, state["profile"]["version"])
            if work is None:
                return
            event_type, payload = work.event_type, work.payload
        try:
            self.queue.send(
                user_id=user_id,
                event_type=event_type,
                payload=payload,
            )
        except Exception:
            if work is None:
                self.store.fail_request(
                    user_id, kind, identity, "MAIL_QUEUE_UNAVAILABLE"
                )
            else:
                self.store.fail_request(
                    user_id, kind, identity, "MAIL_QUEUE_UNAVAILABLE", work
                )
            raise


class DynamoMailStore:
    def __init__(self, table_name: str, client: Any) -> None:
        self.table = table_name
        self.client = client

    def _item(self, user_id: str) -> Mapping[str, Any]:
        response = self.client.get_item(
            TableName=self.table, Key=_key(user_id), ConsistentRead=True
        )
        return response.get("Item") or {}

    def read(self, user_id: str) -> dict[str, Any]:
        return _state(self._item(user_id))

    def connection(self, user_id: str) -> Mapping[str, Any]:
        response = self.client.get_item(
            TableName=self.table,
            Key={
                "PK": {"S": f"USER#{user_id}"},
                "SK": {"S": "CONNECTION#google"},
            },
            ConsistentRead=True,
        )
        return response.get("Item") or {}

    def public_state(self, user_id: str) -> dict[str, Any]:
        item = self._item(user_id)
        state = _state(item)
        connection = self.connection(user_id)
        epoch = _text(connection, "mail_connection_id") or ""
        if _auth_required(connection):
            for kind in ("recommendations", "scan"):
                if (_text(item, f"{kind}_connection_id") or "") != epoch:
                    state[kind] = empty_state()[kind]
                state[kind].update(status="ERROR", error_code="GOOGLE_AUTH_REQUIRED")
            state["scan"]["profile_version"] = state["profile"]["version"]
            return state
        connected = _text(connection, "status") == "CONNECTED"
        waiting = state["scan"]["status"] == "PENDING" and (
            _scan_eligible(connection) or _setup_pending(connection)
        )
        if (not connected and not waiting) or (
            _text(item, "scan_connection_id") or ""
        ) != epoch:
            state["scan"] = empty_scan(state["profile"]["version"])
        recommendation_waiting = state["recommendations"]["status"] == "PENDING" and (
            _scan_eligible(connection) or _setup_pending(connection)
        )
        if (not connected and not recommendation_waiting) or (
            _text(item, "recommendations_connection_id") or ""
        ) != epoch:
            state["recommendations"] = empty_state()["recommendations"]
        setup_pending = _setup_pending(connection)
        setup_failed = (
            _text(connection, "status") == "ERROR"
            and _text(connection, "error_code") == "MAIL_SETUP_FAILED"
            and bool(epoch)
        )
        if setup_pending or setup_failed:
            if state["recommendations"]["status"] == "NOT_STARTED" or setup_failed:
                state["recommendations"] = dict(
                    empty_state()["recommendations"],
                    status="ERROR" if setup_failed else "PENDING",
                    request_id=state["recommendations"]["request_id"] or epoch,
                    error_code="MAIL_SETUP_FAILED" if setup_failed else None,
                )
            if configured(state["profile"]) and (
                state["scan"]["status"] == "NOT_STARTED" or setup_failed
            ):
                state["scan"] = dict(
                    empty_scan(state["profile"]["version"]),
                    status="ERROR" if setup_failed else "PENDING",
                    scan_id=state["scan"]["scan_id"] or epoch,
                    error_code="MAIL_SETUP_FAILED" if setup_failed else None,
                )
        return state

    def connection_guard(
        self, user_id: str, connection: Mapping[str, Any]
    ) -> dict[str, Any]:
        epoch = _text(connection, "mail_connection_id")
        values = {":connected": {"S": _text(connection, "status") or "CONNECTED"}}
        condition = "#status=:connected AND attribute_not_exists(mail_connection_id)"
        if epoch is not None:
            condition = "#status=:connected AND mail_connection_id=:epoch"
            values[":epoch"] = {"S": epoch}
        return {
            "ConditionCheck": {
                "TableName": self.table,
                "Key": {
                    "PK": {"S": f"USER#{user_id}"},
                    "SK": {"S": "CONNECTION#google"},
                },
                "ConditionExpression": condition,
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": values,
            }
        }

    def save_profile(
        self, user_id: str, tags: list[str], description: str, expected: int
    ) -> tuple[dict[str, Any], bool]:
        profile = {
            "tags": tags,
            "description": description,
            "version": expected + 1,
            "updated_at": _now(),
        }
        scan = empty_scan(expected + 1)
        connection = self.connection(user_id)
        setup_pending = _setup_pending(connection)
        eligible = configured(profile) and (_scan_eligible(connection) or setup_pending)
        if eligible:
            scan.update(status="PENDING", scan_id=uuid.uuid4().hex)
        update = {
            "TableName": self.table,
            "Key": _key(user_id),
            "UpdateExpression": "SET profile_json=:profile,profile_version=:next,scan_json=:scan,scan_id=:id,scan_connection_id=:epoch,scan_step=:zero,scan_next_json=:null",
            "ConditionExpression": "attribute_not_exists(profile_version)"
            if expected == 0
            else "profile_version=:expected",
            "ExpressionAttributeValues": {
                ":profile": _json(profile),
                ":next": {"N": str(expected + 1)},
                ":scan": _json(scan),
                ":id": {"S": scan["scan_id"] or ""},
                ":epoch": {"S": _text(connection, "mail_connection_id") or ""},
                ":zero": {"N": "0"},
                ":null": _json(None),
            },
        }
        if expected:
            update["ExpressionAttributeValues"][":expected"] = {"N": str(expected)}
        transactions = [{"Update": update}]
        if eligible:
            transactions.append(self.connection_guard(user_id, connection))
        self._transact(transactions)
        return self.read(user_id), eligible and not setup_pending

    def request(self, user_id: str, kind: str) -> tuple[dict[str, Any], bool]:
        item = self._item(user_id)
        state = _state(item)
        connection = self.connection(user_id)
        if _auth_required(connection):
            return self.public_state(user_id), False
        if _setup_pending(connection):
            if kind == "scan" and not configured(state["profile"]):
                raise MailInputError("Mail interests are not configured")
            return self.public_state(user_id), False
        if not _scan_eligible(connection):
            raise MailInputError("Google is not connected")
        if kind == "scan" and not configured(state["profile"]):
            raise MailInputError("Mail interests are not configured")
        old = state[kind]
        if _pending_request(state, item, connection, kind):
            # An earlier request may have stopped after committing but before
            # dispatch. Explicit retries reuse the durable identity.
            return state, True
        if (
            kind == "scan"
            and old["status"] == "ERROR"
            and old["completed_at"] is None
            and _pending_request(
                {**state, "scan": {**old, "status": "PENDING"}},
                item,
                connection,
                kind,
            )
            and _scan_step(item) > 0
        ):
            _scan_continuation(state, item, connection)
            record = {**old, "status": "PENDING", "error_code": None}
            condition, values = _scan_snapshot(item)
            values[":record"] = _json(record)
            self._transact(
                [
                    {
                        "Update": {
                            "TableName": self.table,
                            "Key": _key(user_id),
                            "UpdateExpression": "SET scan_json=:record",
                            "ConditionExpression": condition,
                            "ExpressionAttributeValues": values,
                        }
                    },
                    self.connection_guard(user_id, connection),
                ]
            )
            return {**state, "scan": record}, True
        identity = uuid.uuid4().hex
        version = state["profile"]["version"]
        if kind == "scan":
            record = empty_scan(version)
            record.update(status="PENDING", scan_id=identity)
        else:
            record = dict(
                empty_state()["recommendations"], status="PENDING", request_id=identity
            )
        id_field = "scan_id" if kind == "scan" else "request_id"
        old_identity = old[id_field]
        condition = (
            f"{id_field}=:old"
            if old_identity is not None
            else f"(attribute_not_exists({id_field}) OR {id_field}=:old)"
        )
        values = {
            ":record": _json(record),
            ":id": {"S": identity},
            ":old": {"S": old_identity or ""},
            ":epoch": {"S": _text(connection, "mail_connection_id") or ""},
        }
        if kind == "scan":
            condition += " AND profile_version=:version"
            values[":version"] = {"N": str(version)}
            snapshot_condition, snapshot_values = _scan_snapshot(item)
            condition += " AND " + snapshot_condition
            values.update(snapshot_values)
        expression = (
            f"SET {kind}_json=:record,{id_field}=:id,{kind}_connection_id=:epoch"
        )
        if kind == "scan":
            expression += ",scan_step=:zero,scan_next_json=:null"
            values.update({":zero": {"N": "0"}, ":null": _json(None)})
        try:
            self._transact(
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
                    self.connection_guard(user_id, connection),
                ]
            )
        except MailConflict:
            current_item = self._item(user_id)
            current = _state(current_item)
            if _pending_request(current, current_item, self.connection(user_id), kind):
                return current, True
            current = self.public_state(user_id)
            if current[kind]["status"] == "PENDING":
                return current, False
            raise
        return self.read(user_id), True

    def scan_work(self, user_id: str, identity: str, version: int) -> _ScanWork | None:
        """Dispatch current durable work, never replay a completed first-page job."""
        item = self._item(user_id)
        state = _state(item)
        scan = state["scan"]
        connection = self.connection(user_id)
        if (
            not _scan_eligible(connection)
            or not configured(state["profile"])
            or scan["status"] != "PENDING"
            or scan["completed_at"] is not None
            or scan["scan_id"] != identity
            or _text(item, "scan_id") != identity
            or scan["profile_version"] != version
            or state["profile"]["version"] != version
            or (_text(item, "scan_connection_id") or "")
            != (_text(connection, "mail_connection_id") or "")
        ):
            return None
        if _scan_step(item) > 0:
            work = _scan_continuation(state, item, connection)
        else:
            if _text(item, "scan_next_json") not in {None, "null"}:
                raise MailConflict("Mail scan continuation is invalid")
            work = {
                "event_type": "MAIL_SCAN_REQUESTED",
                "payload": {"request_id": identity, "profile_version": version},
            }
        return _ScanWork(work["event_type"], work["payload"], item, connection)

    def fail_request(
        self,
        user_id: str,
        kind: str,
        identity: str,
        code: str,
        work: _ScanWork | None = None,
    ) -> None:
        if work is not None:
            scan = _state(work.item)["scan"]
            if (
                kind != "scan"
                or scan["scan_id"] != identity
                or scan["status"] != "PENDING"
            ):
                return
            condition, values = _scan_snapshot(work.item)
            values[":record"] = _json({**scan, "status": "ERROR", "error_code": code})
            try:
                self._transact(
                    [
                        {
                            "Update": {
                                "TableName": self.table,
                                "Key": _key(user_id),
                                "UpdateExpression": "SET scan_json=:record",
                                "ConditionExpression": condition,
                                "ExpressionAttributeValues": values,
                            }
                        },
                        self.connection_guard(user_id, work.connection),
                    ]
                )
            except MailConflict:
                pass
            return
        state = self.read(user_id)
        field = "scan_id" if kind == "scan" else "request_id"
        if state[kind][field] != identity:
            return
        state[kind].update(status="ERROR", error_code=code)
        try:
            self.client.update_item(
                TableName=self.table,
                Key=_key(user_id),
                UpdateExpression=f"SET {kind}_json=:record",
                ConditionExpression=f"{field}=:id",
                ExpressionAttributeValues={
                    ":record": _json(state[kind]),
                    ":id": {"S": identity},
                },
            )
        except self.client.exceptions.ConditionalCheckFailedException:
            return

    def _transact(self, transactions: list[dict[str, Any]]) -> None:
        try:
            self.client.transact_write_items(TransactItems=transactions)
        except self.client.exceptions.TransactionCanceledException as error:
            reasons = getattr(error, "response", {}).get("CancellationReasons", [])
            if any(
                reason.get("Code") == "ConditionalCheckFailed" for reason in reasons
            ):
                raise MailConflict("Mail state changed") from error
            raise

    def results(self, user_id: str, cursor: object) -> dict[str, Any]:
        state = self.public_state(user_id)
        scan = state["scan"]
        result = {
            "profile_version": state["profile"]["version"],
            "scan_id": scan["scan_id"],
            "status": scan["status"],
            "items": [],
            "next_cursor": None,
        }
        if _text(self.connection(user_id), "status") != "CONNECTED":
            return result
        scan_id = scan["scan_id"]
        if scan_id is None:
            if cursor:
                raise MailConflict("Mail result generation changed")
            return result
        owner = hashlib.sha256(user_id.encode()).hexdigest()
        offset = 0
        value = None
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > 4096:
                    raise ValueError
                value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if (
                    value["owner"] != owner
                    or value["scan_id"] != scan_id
                    or value["version"] != result["profile_version"]
                ):
                    raise MailConflict("Mail result generation changed")
                if value.get("order") != MAIL_RESULT_ORDER:
                    raise MailConflict("Mail result ordering changed")
                offset = value["offset"]
                if type(offset) is not int or not 1 <= offset <= MAX_MAIL_RESULTS:
                    raise ValueError
                if not isinstance(value["fingerprint"], str):
                    raise TypeError
            except (ValueError, KeyError, TypeError) as error:
                raise MailInputError("Mail cursor is invalid") from error

        snapshot = self._item(user_id)
        connection_id = _text(snapshot, "scan_connection_id") or ""
        if value is not None and value.get("connection_id") != connection_id:
            raise MailConflict("Mail result generation changed")
        prefix = f"MAIL_RESULT#{scan_id}#"
        pk = {"S": f"USER#{user_id}"}
        request: dict[str, Any] = {
            "TableName": self.table,
            "KeyConditionExpression": "PK=:pk AND begins_with(SK,:prefix)",
            "ExpressionAttributeValues": {":pk": pk, ":prefix": {"S": prefix}},
            "ConsistentRead": True,
        }
        items: list[dict[str, Any]] = []
        for _ in range(MAX_MAIL_RESULTS + 1):
            request["Limit"] = MAX_MAIL_RESULTS + 1 - len(items)
            response = self.client.query(**request)
            page = response.get("Items", [])
            for item in page:
                if item.get("PK") != pk or not (_text(item, "SK") or "").startswith(
                    prefix
                ):
                    raise ValueError("Mail result storage scope is invalid")
                items.append(json.loads(item["result_json"]["S"]))
            if len(items) > MAX_MAIL_RESULTS:
                raise MailConflict("Mail result set exceeds its ordering bound")
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            if (
                not page
                or last_key.get("PK") != pk
                or not (_text(last_key, "SK") or "").startswith(prefix)
                or (_text(last_key, "SK") or "")
                <= (_text(request.get("ExclusiveStartKey", {}), "SK") or "")
            ):
                raise MailConflict("Mail result pagination did not advance")
            request["ExclusiveStartKey"] = last_key
        else:
            raise MailConflict("Mail result read exceeds its ordering bound")

        items.sort(key=_result_order)
        if len({item["evidence_ref"] for item in items}) != len(items):
            raise ValueError("Mail result references are duplicated")
        fingerprint = hashlib.sha256(
            json.dumps(items, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if value is not None:
            if value["fingerprint"] != fingerprint:
                raise MailConflict("Mail results changed during pagination")
            if offset >= len(items):
                raise MailInputError("Mail cursor is invalid")
        result["items"] = items[offset : offset + MAIL_RESULTS_PAGE_SIZE]
        next_offset = offset + len(result["items"])
        if next_offset < len(items):
            result["next_cursor"] = base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "owner": owner,
                        "scan_id": scan_id,
                        "version": result["profile_version"],
                        "connection_id": connection_id,
                        "order": MAIL_RESULT_ORDER,
                        "fingerprint": fingerprint,
                        "offset": next_offset,
                    },
                    separators=(",", ":"),
                ).encode()
            ).decode()
        # An edit during the query must not publish a previous generation.
        current = self.public_state(user_id)
        latest = self._item(user_id)
        if (
            current["profile"]["version"] != result["profile_version"]
            or current["scan"]["scan_id"] != scan_id
            or any(
                snapshot.get(field) != latest.get(field)
                for field in (
                    "profile_version",
                    "scan_id",
                    "scan_connection_id",
                    "scan_step",
                )
            )
            or _text(self.connection(user_id), "status") != "CONNECTED"
        ):
            raise MailConflict("Mail result generation changed")
        return result


def default_mail_service() -> MailInterestService:
    import boto3

    from .connections import SqsWorkQueue, _required_environment

    return MailInterestService(
        DynamoMailStore(
            _required_environment("MAIN_TABLE_NAME"), boto3.client("dynamodb")
        ),
        SqsWorkQueue.from_environment(),
    )
