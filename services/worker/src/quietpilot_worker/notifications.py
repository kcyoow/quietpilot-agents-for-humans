"""Durable, attention-only push outbox. Acceptance is not device delivery."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

EXPO_SEND_URL = "https://exp.host/--/api/v2/push/send"
MAX_EVENTS = 5
MAX_DEVICES = 5
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_DEVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}")
_TOKEN = re.compile(r"(?:ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]{8,200}\]")
_KINDS = {
    "DECISION_REQUIRED": {"DECISION_REQUIRED"},
    "ACTION_FAILED": {"FAILED", "PERMISSION_REVOKED"},
    "PLAN_CHANGED": {"DECISION_REQUIRED"},
}
_BODIES = {
    "DECISION_REQUIRED": "A task needs your review. Open the app to continue.",
    "ACTION_FAILED": "A task could not finish. Open the app for details.",
    "PLAN_CHANGED": "The plan changed. Open the app to review it.",
}


def notification_put(
    table: str, user_id: str, case_id: str, version: int, kind: str, now: str | float
) -> dict[str, Any]:
    """Append this Put to the Case transaction; version is its resulting META version."""
    _owner(user_id)
    _identity(case_id)
    if (
        not isinstance(table, str)
        or not table
        or type(version) is not int
        or not 1 <= version <= 2_147_483_647
        or not isinstance(kind, str)
        or kind not in _KINDS
    ):
        raise ValueError("Notification identity or kind is invalid")
    stamp, epoch = _time(now)
    event_id = _event_id(user_id, case_id, version, kind)
    return {
        "Put": {
            "TableName": table,
            "Item": {
                **_key(user_id, f"NOTIFICATION#{event_id}"),
                "entity_type": {"S": "notification"},
                "user_id": {"S": user_id},
                "case_id": {"S": case_id},
                "version": {"N": str(version)},
                "kind": {"S": kind},
                "event_id": {"S": event_id},
                "status": {"S": "PENDING"},
                "created_at": {"S": stamp},
                "expiresAt": {"N": str(int(epoch) + 30 * 86_400)},
                "GSI1PK": {"S": f"USER#{user_id}#NOTIFICATION#PENDING"},
                "GSI1SK": {"S": f"{stamp}#{event_id}"},
            },
            "ConditionExpression": "attribute_not_exists(PK)",
        }
    }


@dataclass(frozen=True)
class PushOutcome:
    status: str
    code: str


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ExpoPushTransport:
    """One fixed-endpoint request, no redirect or retry, no raw error reporting."""

    def __init__(self, *, opener: Any = None, timeout: float = 3.0) -> None:
        if not 0 < timeout <= 5:
            raise ValueError("Push timeout is invalid")
        self.opener = opener if opener is not None else build_opener(_NoRedirect())
        self.timeout = timeout

    def __call__(self, message: Mapping[str, object]) -> PushOutcome:
        payload = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        ).encode()
        if len(payload) > 4096:
            return PushOutcome("REJECTED", "PAYLOAD_TOO_LARGE")
        request = Request(
            EXPO_SEND_URL,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    return _http_outcome(response.status)
                body = response.read(16_385)
            if len(body) > 16_384:
                return PushOutcome("UNKNOWN", "INVALID_PROVIDER_RESPONSE")
            data = json.loads(body)
            if not isinstance(data, dict) or data.get("errors"):
                return PushOutcome("UNKNOWN", "INVALID_PROVIDER_RESPONSE")
            ticket = data.get("data")
            if isinstance(ticket, list) and len(ticket) == 1:
                ticket = ticket[0]
            if not isinstance(ticket, dict):
                return PushOutcome("UNKNOWN", "INVALID_PROVIDER_RESPONSE")
            ticket_id = ticket.get("id")
            if (
                ticket.get("status") == "ok"
                and isinstance(ticket_id, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", ticket_id)
            ):
                return PushOutcome("ACCEPTED", "EXPO_TICKET_ACCEPTED")
            if ticket.get("status") == "error":
                details = ticket.get("details")
                code = details.get("error") if isinstance(details, dict) else None
                return PushOutcome(
                    "REJECTED",
                    "DEVICE_NOT_REGISTERED"
                    if code == "DeviceNotRegistered"
                    else "PROVIDER_REJECTED",
                )
            return PushOutcome("UNKNOWN", "INVALID_PROVIDER_RESPONSE")
        except HTTPError as error:
            return _http_outcome(error.code)
        except (OSError, ValueError, HTTPException):
            return PushOutcome("UNKNOWN", "TRANSPORT_UNCONFIRMED")


class NotificationDispatcher:
    """Drains at most five events, with five device attempts per event.

    A SENDING event is never reclaimed for another send: after its lease expires,
    it becomes UNKNOWN. This intentionally favors app refetch over duplicate push.
    Registrations/Case state are checked transactionally immediately before send;
    an already in-flight provider request cannot be recalled on unregister.
    """

    def __init__(
        self,
        table_name: str,
        client: Any,
        *,
        clock: Callable[[], float] = time.time,
        transport: Callable[[Mapping[str, object]], PushOutcome] | None = None,
    ) -> None:
        if not isinstance(table_name, str) or not table_name:
            raise ValueError("Notification table is required")
        self.table = table_name
        self.client = client
        self.clock = clock
        self.transport = transport if transport is not None else ExpoPushTransport()

    def flush(self, user_id: str, *, event_id: str | None = None) -> dict[str, int]:
        _owner(user_id)
        counts = dict.fromkeys(
            ("accepted", "rejected", "unknown", "suppressed", "busy"), 0
        )
        if event_id is not None:
            if (
                not isinstance(event_id, str)
                or re.fullmatch(r"[0-9a-f]{64}", event_id) is None
            ):
                raise ValueError("Notification event identifier is invalid")
            event = self._get(_key(user_id, f"NOTIFICATION#{event_id}"))
            if event and _s(event, "status") in {"PENDING", "SENDING"}:
                counts[self._process(user_id, event).lower()] += 1
            return counts
        page = self.client.query(
            TableName=self.table,
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK=:owner",
            ExpressionAttributeValues={
                ":owner": {"S": f"USER#{user_id}#NOTIFICATION#PENDING"}
            },
            Limit=MAX_EVENTS,
        )
        for candidate in page.get("Items", [])[:MAX_EVENTS]:
            suffix = _s(candidate, "SK")
            if (
                _s(candidate, "PK") != f"USER#{user_id}"
                or not isinstance(suffix, str)
                or re.fullmatch(r"NOTIFICATION#[0-9a-f]{64}", suffix) is None
            ):
                continue
            event = self._get(_key(user_id, suffix))
            result = self._process(user_id, event)
            counts[result.lower()] += 1
        return counts

    def _process(self, user_id: str, event: dict[str, Any]) -> str:
        if _s(event, "user_id") != user_id:
            return "BUSY"
        status = _s(event, "status")
        if status == "SENDING":
            if _n(event, "lease_until") > self.clock():
                return "BUSY"
            self._finish(event, "UNKNOWN", "INTERRUPTED_SEND", {})
            return "UNKNOWN"
        if status != "PENDING":
            return "BUSY"
        case_id, kind = _s(event, "case_id"), _s(event, "kind")
        version = _n(event, "version")
        if (
            not isinstance(case_id, str)
            or _IDENTITY.fullmatch(case_id) is None
            or kind not in _KINDS
            or version < 1
            or _s(event, "event_id") != _event_id(user_id, case_id, version, kind)
            or _n(event, "expiresAt") <= self.clock()
            or not self._attention(event)
        ):
            self._finish(event, "SUPPRESSED", "NO_CURRENT_ATTENTION", {})
            return "SUPPRESSED"
        claimed = self._claim(event)
        if not claimed:
            return "BUSY"
        counts = dict.fromkeys(("accepted", "rejected", "unknown", "suppressed"), 0)
        registry = self._get(_key(user_id, "PUSH_REGISTRY"))
        devices = registry.get("device_ids", {}).get("L", [])
        if not isinstance(devices, list) or len(devices) > MAX_DEVICES:
            devices = []
        for candidate in devices:
            device_id = candidate.get("S") if isinstance(candidate, dict) else None
            if not isinstance(device_id, str) or _DEVICE.fullmatch(device_id) is None:
                counts["suppressed"] += 1
                continue
            device = self._get(_key(user_id, f"PUSH_DEVICE#{device_id}"))
            token = _s(device, "expo_push_token")
            digest = _s(device, "token_hash")
            if (
                not isinstance(token, str)
                or _TOKEN.fullmatch(token) is None
                or hashlib.sha256(token.encode()).hexdigest() != digest
                or not self._attention(event)
                or not self._permit(claimed, device)
            ):
                counts["suppressed"] += 1
                continue
            message = {
                "to": token,
                "title": "Review needed",
                "body": _BODIES[kind],
                "data": {
                    "case_id": case_id,
                    "event_id": _s(event, "event_id"),
                    "kind": kind,
                },
                "channelId": "default",
                "ttl": 300,
            }
            try:
                result = self.transport(message)
                if not isinstance(result, PushOutcome) or result.status not in {
                    "ACCEPTED",
                    "REJECTED",
                    "UNKNOWN",
                }:
                    result = PushOutcome("UNKNOWN", "INVALID_TRANSPORT_RESULT")
            except Exception:  # noqa: BLE001 - an uncertain send must never be replayed.
                result = PushOutcome("UNKNOWN", "TRANSPORT_UNCONFIRMED")
            counts[result.status.lower()] += 1
            if result.status == "REJECTED" and result.code == "DEVICE_NOT_REGISTERED":
                self._disable(device)
        final = (
            "UNKNOWN"
            if counts["unknown"]
            else "ACCEPTED"
            if counts["accepted"]
            else "REJECTED"
            if counts["rejected"]
            else "SUPPRESSED"
        )
        self._finish(
            claimed,
            final,
            {
                "ACCEPTED": "EXPO_TICKET_ACCEPTED",
                "REJECTED": "EXPO_TICKET_REJECTED",
                "UNKNOWN": "DELIVERY_UNCONFIRMED",
                "SUPPRESSED": "NO_ELIGIBLE_DEVICE",
            }[final],
            counts,
        )
        return final

    def _attention(self, event: dict[str, Any]) -> bool:
        case = self._get(_case_key(_s(event, "case_id")))
        return (
            _s(case, "entity_type") == "case"
            and _s(case, "user_id") == _s(event, "user_id")
            and _s(case, "case_id") == _s(event, "case_id")
            and _n(case, "version") == _n(event, "version")
            and _s(case, "status") in _KINDS.get(_s(event, "kind"), set())
        )

    def _case_check(self, event: dict[str, Any]) -> dict[str, Any]:
        statuses = sorted(_KINDS[_s(event, "kind")])
        return {
            "ConditionCheck": {
                "TableName": self.table,
                "Key": _case_key(_s(event, "case_id")),
                "ConditionExpression": "user_id=:owner AND case_id=:case AND entity_type=:entity AND #version=:version AND #status IN ("
                + ",".join(f":s{i}" for i in range(len(statuses)))
                + ")",
                "ExpressionAttributeNames": {
                    "#version": "version",
                    "#status": "status",
                },
                "ExpressionAttributeValues": {
                    ":owner": event["user_id"],
                    ":case": event["case_id"],
                    ":entity": {"S": "case"},
                    ":version": event["version"],
                    **{f":s{i}": {"S": s} for i, s in enumerate(statuses)},
                },
            }
        }

    def _claim(self, event: dict[str, Any]) -> dict[str, Any] | None:
        claim = dict(event, status={"S": "SENDING"})
        claim["lease_token"] = {"S": uuid.uuid4().hex}
        claim["lease_until"] = {"N": str(int(self.clock()) + 60)}
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table,
                            "Item": claim,
                            "ConditionExpression": "#status=:pending AND user_id=:owner",
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": {
                                ":pending": {"S": "PENDING"},
                                ":owner": event["user_id"],
                            },
                        }
                    },
                    self._case_check(event),
                ]
            )
            return claim
        except Exception as error:
            if _conditional(error):
                return None
            raise

    def _permit(self, event: dict[str, Any], device: dict[str, Any]) -> bool:
        if (
            _s(device, "user_id") != _s(event, "user_id")
            or _s(device, "platform") != "android"
            or device.get("enabled") != {"BOOL": True}
            or not _s(device, "binding_id")
        ):
            return False
        condition = "user_id=:owner AND device_id=:device AND binding_id=:binding AND enabled=:enabled"
        values = {
            ":owner": event["user_id"],
            ":device": device["device_id"],
            ":binding": device["binding_id"],
            ":enabled": {"BOOL": True},
        }
        try:
            self.client.transact_write_items(
                TransactItems=[
                    self._case_check(event),
                    *[
                        {
                            "ConditionCheck": {
                                "TableName": self.table,
                                "Key": key,
                                "ConditionExpression": condition,
                                "ExpressionAttributeValues": values,
                            }
                        }
                        for key in (
                            {"PK": device["PK"], "SK": device["SK"]},
                            _binding_key(_s(device, "token_hash")),
                        )
                    ],
                    {
                        "Update": {
                            "TableName": self.table,
                            "Key": {"PK": event["PK"], "SK": event["SK"]},
                            "UpdateExpression": "SET last_attempt_at=:now ADD send_attempts :one",
                            "ConditionExpression": "#status=:sending AND lease_token=:lease AND lease_until>:epoch",
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": {
                                ":now": {"S": _time(self.clock())[0]},
                                ":one": {"N": "1"},
                                ":sending": {"S": "SENDING"},
                                ":lease": event["lease_token"],
                                ":epoch": {"N": str(int(self.clock()))},
                            },
                        }
                    },
                ]
            )
            return True
        except Exception as error:
            if _conditional(error):
                return False
            raise

    def _disable(self, device: dict[str, Any]) -> None:
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.table,
                            "Key": key,
                            "UpdateExpression": "SET enabled=:disabled REMOVE expo_push_token",
                            "ConditionExpression": "binding_id=:binding AND user_id=:owner AND device_id=:device",
                            "ExpressionAttributeValues": {
                                ":disabled": {"BOOL": False},
                                ":binding": device["binding_id"],
                                ":owner": device["user_id"],
                                ":device": device["device_id"],
                            },
                        }
                    }
                    for key in (
                        {"PK": device["PK"], "SK": device["SK"]},
                        _binding_key(_s(device, "token_hash")),
                    )
                ]
            )
        except Exception as error:
            if not _conditional(error):
                raise

    def _finish(
        self, event: dict[str, Any], status: str, reason: str, counts: dict[str, int]
    ) -> None:
        values = {
            ":status": {"S": status},
            ":expected": event["status"],
            ":owner": event["user_id"],
            ":now": {"S": _time(self.clock())[0]},
            ":reason": {"S": reason},
        }
        condition = "#status=:expected AND user_id=:owner"
        if "lease_token" in event:
            condition += " AND lease_token=:lease"
            values[":lease"] = event["lease_token"]
        for name in ("accepted", "rejected", "unknown", "suppressed"):
            values[f":{name}"] = {"N": str(counts.get(name, 0))}
        try:
            self.client.update_item(
                TableName=self.table,
                Key={"PK": event["PK"], "SK": event["SK"]},
                UpdateExpression="SET #status=:status, updated_at=:now, reason_code=:reason, "
                + ", ".join(
                    f"{name}_count=:{name}"
                    for name in ("accepted", "rejected", "unknown", "suppressed")
                )
                + " REMOVE GSI1PK,GSI1SK,lease_token,lease_until",
                ConditionExpression=condition,
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values,
            )
        except Exception as error:
            if not _conditional(error):
                raise

    def _get(self, key: dict[str, Any]) -> dict[str, Any]:
        return self.client.get_item(
            TableName=self.table, Key=key, ConsistentRead=True
        ).get("Item", {})


def _http_outcome(status: int) -> PushOutcome:
    return (
        PushOutcome("REJECTED", "PROVIDER_REJECTED")
        if 400 <= status < 500
        else PushOutcome("UNKNOWN", "TRANSPORT_UNCONFIRMED")
    )


def _event_id(user: str, case: str, version: int, kind: str) -> str:
    return hashlib.sha256(
        json.dumps([user, case, version, kind], separators=(",", ":")).encode()
    ).hexdigest()


def _identity(value: object) -> None:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ValueError("Notification identity is invalid")


def _owner(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("Notification owner is invalid")


def _key(user: str, suffix: str) -> dict[str, Any]:
    return {"PK": {"S": f"USER#{user}"}, "SK": {"S": suffix}}


def _case_key(case: str) -> dict[str, Any]:
    return {"PK": {"S": f"CASE#{case}"}, "SK": {"S": "META"}}


def _binding_key(digest: str) -> dict[str, Any]:
    return {"PK": {"S": f"PUSH_TOKEN#{digest}"}, "SK": {"S": "META"}}


def _s(item: Mapping[str, Any], name: str) -> str | None:
    return item.get(name, {}).get("S")


def _n(item: Mapping[str, Any], name: str) -> int:
    value = item.get(name, {}).get("N", "")
    return (
        int(value) if isinstance(value, str) and re.fullmatch(r"[0-9]+", value) else 0
    )


def _time(value: str | float) -> tuple[str, float]:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("Notification timestamp needs a timezone")
        epoch = parsed.timestamp()
    elif type(value) in {int, float} and math.isfinite(value):
        epoch = value
    else:
        raise ValueError("Notification timestamp is invalid")
    return datetime.fromtimestamp(epoch, UTC).isoformat(), epoch


def _conditional(error: Exception) -> bool:
    return getattr(error, "response", {}).get("Error", {}).get("Code") in {
        "ConditionalCheckFailedException",
        "TransactionCanceledException",
    }
