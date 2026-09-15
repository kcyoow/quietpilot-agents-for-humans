"""DynamoDB-backed idempotency gate for at-least-once worker delivery."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

EventProcessor = Callable[[dict[str, object]], None]
Clock = Callable[[], float]
TokenFactory = Callable[[], str]

COMPLETED_TTL_SECONDS = 7 * 24 * 60 * 60


class IdempotencyConflict(RuntimeError):
    """The same dedupe key was reused for materially different input."""


class IdempotencyInProgress(RuntimeError):
    """Another invocation still owns the active processing lease."""


class IdempotencyLeaseLost(RuntimeError):
    """The current invocation no longer owns its processing lease."""


class IdempotencyGate(Protocol):
    def run(self, envelope: dict[str, object], processor: EventProcessor) -> bool: ...


@dataclass(frozen=True, slots=True)
class _Claim:
    key: dict[str, dict[str, str]]
    token: str


class DynamoIdempotencyStore:
    """Claim, complete and retry one validated event without storing its payload."""

    def __init__(
        self,
        table_name: str,
        client: Any,
        *,
        in_progress_ttl_seconds: int,
        completed_ttl_seconds: int = COMPLETED_TTL_SECONDS,
        clock: Clock = time.time,
        token_factory: TokenFactory = lambda: secrets.token_hex(16),
    ) -> None:
        if not table_name.strip():
            raise ValueError("idempotency table name is required")
        if in_progress_ttl_seconds < 1 or completed_ttl_seconds < 1:
            raise ValueError("idempotency expirations must be positive")
        self._table_name = table_name
        self._client = client
        self._in_progress_ttl_seconds = in_progress_ttl_seconds
        self._completed_ttl_seconds = completed_ttl_seconds
        self._clock = clock
        self._token_factory = token_factory

    @classmethod
    def from_environment(
        cls,
        *,
        in_progress_ttl_seconds: int,
    ) -> DynamoIdempotencyStore:
        import boto3

        return cls(
            _required_environment("IDEMPOTENCY_TABLE_NAME"),
            boto3.client("dynamodb"),
            in_progress_ttl_seconds=in_progress_ttl_seconds,
        )

    def run(self, envelope: dict[str, object], processor: EventProcessor) -> bool:
        """Run once; return False when an unexpired completion already exists."""

        user_id = _bounded_text(envelope, "user_id", maximum=256)
        dedupe_key = _bounded_text(envelope, "dedupe_key", maximum=2048)
        event_type = _bounded_text(envelope, "event_type", maximum=128)
        payload_hash = _payload_hash(envelope)
        claim = self._claim(
            user_id=user_id,
            dedupe_key=dedupe_key,
            event_type=event_type,
            payload_hash=payload_hash,
        )
        if claim is None:
            return False
        try:
            processor(envelope)
        except Exception:
            self._mark_failed(claim)
            raise
        self._mark_completed(claim)
        return True

    def _claim(
        self,
        *,
        user_id: str,
        dedupe_key: str,
        event_type: str,
        payload_hash: str,
    ) -> _Claim | None:
        now = int(self._clock())
        token = self._token_factory()
        key = _record_key(user_id, dedupe_key)
        item = {
            **key,
            "status": {"S": "IN_PROGRESS"},
            "payloadHash": {"S": payload_hash},
            "eventType": {"S": event_type},
            "lockToken": {"S": token},
            "inProgressExpiresAt": {"N": str(now + self._in_progress_ttl_seconds)},
            "expiresAt": {"N": str(now + self._completed_ttl_seconds)},
            "updatedAt": {"N": str(now)},
        }
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(subject)",
            )
            return _Claim(key=key, token=token)
        except self._client.exceptions.ConditionalCheckFailedException:
            existing = self._load(key)

        if existing is None:
            raise IdempotencyInProgress("idempotency record changed during claim")
        if _string_attribute(existing, "payloadHash") != payload_hash:
            raise IdempotencyConflict("dedupe key payload does not match")

        status = _string_attribute(existing, "status")
        expires_at = _integer_attribute(existing, "expiresAt") or 0
        in_progress_expires_at = (
            _integer_attribute(existing, "inProgressExpiresAt") or 0
        )
        if status == "COMPLETED" and expires_at > now:
            return None
        if status == "IN_PROGRESS" and in_progress_expires_at > now:
            raise IdempotencyInProgress("event processing is still in progress")

        try:
            self._client.update_item(
                TableName=self._table_name,
                Key=key,
                UpdateExpression=(
                    "SET #status=:in_progress, lockToken=:token, "
                    "inProgressExpiresAt=:in_progress_expiry, "
                    "expiresAt=:expiry, updatedAt=:updated"
                ),
                ConditionExpression=(
                    "payloadHash=:payload_hash AND (expiresAt<=:now OR "
                    "#status=:failed OR "
                    "(#status=:in_progress AND inProgressExpiresAt<=:now))"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":in_progress": {"S": "IN_PROGRESS"},
                    ":failed": {"S": "FAILED"},
                    ":payload_hash": {"S": payload_hash},
                    ":token": {"S": token},
                    ":in_progress_expiry": {
                        "N": str(now + self._in_progress_ttl_seconds)
                    },
                    ":expiry": {"N": str(now + self._completed_ttl_seconds)},
                    ":updated": {"N": str(now)},
                    ":now": {"N": str(now)},
                },
            )
        except self._client.exceptions.ConditionalCheckFailedException as error:
            raise IdempotencyInProgress(
                "another invocation acquired the event"
            ) from error
        return _Claim(key=key, token=token)

    def _load(
        self,
        key: dict[str, dict[str, str]],
    ) -> Mapping[str, Any] | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=key,
            ConsistentRead=True,
        )
        item = response.get("Item")
        return item if isinstance(item, Mapping) else None

    def _mark_completed(self, claim: _Claim) -> None:
        now = int(self._clock())
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key=claim.key,
                UpdateExpression=(
                    "SET #status=:completed, expiresAt=:expiry, updatedAt=:updated "
                    "REMOVE lockToken, inProgressExpiresAt"
                ),
                ConditionExpression="#status=:in_progress AND lockToken=:token",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":completed": {"S": "COMPLETED"},
                    ":in_progress": {"S": "IN_PROGRESS"},
                    ":token": {"S": claim.token},
                    ":expiry": {"N": str(now + self._completed_ttl_seconds)},
                    ":updated": {"N": str(now)},
                },
            )
        except self._client.exceptions.ConditionalCheckFailedException as error:
            raise IdempotencyLeaseLost(
                "event completion lease is no longer owned"
            ) from error

    def _mark_failed(self, claim: _Claim) -> None:
        now = int(self._clock())
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key=claim.key,
                UpdateExpression=(
                    "SET #status=:failed, inProgressExpiresAt=:now, "
                    "expiresAt=:expiry, updatedAt=:updated REMOVE lockToken"
                ),
                ConditionExpression="#status=:in_progress AND lockToken=:token",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":failed": {"S": "FAILED"},
                    ":in_progress": {"S": "IN_PROGRESS"},
                    ":token": {"S": claim.token},
                    ":now": {"N": str(now)},
                    ":expiry": {"N": str(now + self._completed_ttl_seconds)},
                    ":updated": {"N": str(now)},
                },
            )
        except self._client.exceptions.ConditionalCheckFailedException as error:
            raise IdempotencyLeaseLost(
                "failed event lease is no longer owned"
            ) from error


def _record_key(user_id: str, dedupe_key: str) -> dict[str, dict[str, str]]:
    return {
        "subject": {
            "S": f"worker#{hashlib.sha256(user_id.encode('utf-8')).hexdigest()}"
        },
        "idempotencyKey": {"S": hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()},
    }


def _payload_hash(envelope: Mapping[str, object]) -> str:
    material = {
        name: envelope.get(name)
        for name in ("schema_version", "event_type", "user_id", "connector", "payload")
    }
    canonical = json.dumps(
        material,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _bounded_text(
    envelope: Mapping[str, object],
    name: str,
    *,
    maximum: int,
) -> str:
    value = envelope.get(name)
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{name} is invalid")
    return value


def _string_attribute(item: Mapping[str, Any], name: str) -> str | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("S"), str):
        return value["S"]
    return None


def _integer_attribute(item: Mapping[str, Any], name: str) -> int | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("N"), str):
        return int(value["N"])
    return None


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value
