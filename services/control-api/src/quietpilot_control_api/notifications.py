"""Owner-scoped push registrations; raw bearer tokens never leave this store."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

MAX_DEVICES = 5
_DEVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}")
_TOKEN = re.compile(r"(?:ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]{8,200}\]")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}")


class PushTokenInputError(ValueError):
    pass


class PushTokenConflict(RuntimeError):
    pass


class PushTokenService:
    """The caller supplies only an authenticated owner; body ownership is forbidden.

    Registry changes use one transaction, including the global token binding.
    Five device slots are available per owner; unregister releases a slot.
    """

    def __init__(
        self, table_name: str, client: Any, *, clock: Callable[[], float] = time.time
    ) -> None:
        if not isinstance(table_name, str) or not table_name:
            raise PushTokenInputError("Push registry table is required")
        self.table = table_name
        self.client = client
        self.clock = clock

    def register(
        self,
        user_id: str,
        *,
        expo_push_token: str,
        device_id: str,
        app_version: str,
        platform: str,
    ) -> dict[str, object]:
        _validate_owner(user_id)
        _validate(device_id, _DEVICE, "device")
        _validate(expo_push_token, _TOKEN, "push token")
        _validate(app_version, _VERSION, "app version")
        if platform != "android":
            raise PushTokenInputError("Push platform is invalid")
        digest = hashlib.sha256(expo_push_token.encode()).hexdigest()
        initial_scope = None
        for _ in range(3):
            registry = self._get(_key(user_id, "PUSH_REGISTRY"))
            devices = _devices(registry)
            current = self._get(_key(user_id, f"PUSH_DEVICE#{device_id}"))
            if device_id not in devices and len(devices) >= MAX_DEVICES:
                raise PushTokenConflict("Unregister a device before adding another")
            expected = {
                "user_id": {"S": user_id},
                "device_id": {"S": device_id},
                "expo_push_token": {"S": expo_push_token},
                "token_hash": {"S": digest},
                "enabled": {"BOOL": True},
                "app_version": {"S": app_version},
                "platform": {"S": platform},
            }
            if (
                device_id in devices
                and _s(current, "binding_id")
                and all(
                    current.get(field) == value for field, value in expected.items()
                )
            ):
                # App resume must not rotate a valid generation or reclaim a token
                # that moved to another owner while this registration was in flight.
                return self.state(user_id, device_id=device_id)
            binding_key = _binding_key(digest)
            previous_binding = self._get(binding_key)
            scope = (_binding_state(current), _binding_state(previous_binding))
            if initial_scope is None:
                initial_scope = scope
            elif scope != initial_scope:
                raise PushTokenConflict(
                    "Push registration changed; refresh and try again"
                )
            binding_id = uuid.uuid4().hex
            now = datetime.fromtimestamp(self.clock(), UTC).isoformat()
            item = {
                **_key(user_id, f"PUSH_DEVICE#{device_id}"),
                "entity_type": {"S": "push_device"},
                "user_id": {"S": user_id},
                "device_id": {"S": device_id},
                "expo_push_token": {"S": expo_push_token},
                "token_hash": {"S": digest},
                "binding_id": {"S": binding_id},
                "enabled": {"BOOL": True},
                "app_version": {"S": app_version},
                "platform": {"S": platform},
                "updated_at": {"S": now},
            }
            binding = {
                **binding_key,
                "entity_type": {"S": "push_token_binding"},
                "user_id": {"S": user_id},
                "device_id": {"S": device_id},
                "binding_id": {"S": binding_id},
                "enabled": {"BOOL": True},
                "updated_at": {"S": now},
            }
            transaction = [
                self._registry_put(user_id, registry, [*devices, device_id]),
                {"Put": {"TableName": self.table, "Item": item, **_cas(current)}},
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": binding,
                        **_cas(previous_binding),
                    }
                },
            ]
            old_hash = _s(current, "token_hash")
            if old_hash and old_hash != digest:
                self._append_binding_disable(transaction, current, old_hash)
            try:
                self.client.transact_write_items(TransactItems=transaction)
                return _public(item, True)
            except Exception as error:
                if not _conditional(error):
                    raise
        raise PushTokenConflict("Push registration changed; refresh and try again")

    def unregister(self, user_id: str, *, device_id: str) -> dict[str, object]:
        _validate_owner(user_id)
        _validate(device_id, _DEVICE, "device")
        for _ in range(3):
            registry = self._get(_key(user_id, "PUSH_REGISTRY"))
            current = self._get(_key(user_id, f"PUSH_DEVICE#{device_id}"))
            if not current:
                return {"device_id": device_id, "registered": False}
            transaction = [
                self._registry_put(
                    user_id, registry, [d for d in _devices(registry) if d != device_id]
                ),
                self._tombstone(current),
            ]
            digest = _s(current, "token_hash")
            if digest:
                self._append_binding_disable(transaction, current, digest)
            try:
                self.client.transact_write_items(TransactItems=transaction)
                return {"device_id": device_id, "registered": False}
            except Exception as error:
                if not _conditional(error):
                    raise
        raise PushTokenConflict("Push registration changed; refresh and try again")

    def state(self, user_id: str, *, device_id: str) -> dict[str, object]:
        _validate_owner(user_id)
        _validate(device_id, _DEVICE, "device")
        item = self._get(_key(user_id, f"PUSH_DEVICE#{device_id}"))
        digest = _s(item, "token_hash")
        if not item or not digest:
            return {"device_id": device_id, "registered": False}
        binding = self._get(_binding_key(digest))
        enabled = (
            _s(item, "user_id") == user_id
            and item.get("enabled") == {"BOOL": True}
            and binding.get("enabled") == {"BOOL": True}
            and _matches(binding, item)
        )
        return _public(item, enabled)

    def _get(self, key: dict[str, Any]) -> dict[str, Any]:
        return self.client.get_item(
            TableName=self.table, Key=key, ConsistentRead=True
        ).get("Item", {})

    def _registry_put(
        self, user_id: str, current: dict[str, Any], devices: list[str]
    ) -> dict[str, Any]:
        version = int(current.get("version", {"N": "0"})["N"])
        item = {
            **_key(user_id, "PUSH_REGISTRY"),
            "entity_type": {"S": "push_registry"},
            "user_id": {"S": user_id},
            "version": {"N": str(version + 1)},
            "device_ids": {"L": [{"S": d} for d in dict.fromkeys(devices)]},
        }
        guard = (
            {
                "ConditionExpression": "#version=:version",
                "ExpressionAttributeNames": {"#version": "version"},
                "ExpressionAttributeValues": {":version": {"N": str(version)}},
            }
            if current
            else {"ConditionExpression": "attribute_not_exists(PK)"}
        )
        return {"Put": {"TableName": self.table, "Item": item, **guard}}

    def _append_binding_disable(
        self, transaction: list[dict[str, Any]], item: dict[str, Any], digest: str
    ) -> None:
        binding = self._get(_binding_key(digest))
        if _matches(binding, item):
            transaction.append(self._tombstone(binding))

    def _tombstone(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "Update": {
                "TableName": self.table,
                "Key": {"PK": item["PK"], "SK": item["SK"]},
                "UpdateExpression": "SET enabled=:disabled, updated_at=:now REMOVE expo_push_token",
                "ConditionExpression": "binding_id=:binding AND user_id=:owner AND device_id=:device",
                "ExpressionAttributeValues": {
                    ":disabled": {"BOOL": False},
                    ":now": {
                        "S": datetime.fromtimestamp(self.clock(), UTC).isoformat()
                    },
                    ":binding": item["binding_id"],
                    ":owner": item["user_id"],
                    ":device": item["device_id"],
                },
            }
        }


def _validate(value: object, pattern: re.Pattern[str], label: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PushTokenInputError(f"Push {label} is invalid")


def _validate_owner(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise PushTokenInputError("Push owner is invalid")


def _binding_state(item: dict[str, Any]) -> tuple[object, ...]:
    return (
        *(_s(item, field) for field in ("user_id", "device_id", "binding_id")),
        item.get("enabled", {}).get("BOOL"),
    )


def _key(user_id: str, suffix: str) -> dict[str, Any]:
    return {"PK": {"S": f"USER#{user_id}"}, "SK": {"S": suffix}}


def _binding_key(digest: str) -> dict[str, Any]:
    return {"PK": {"S": f"PUSH_TOKEN#{digest}"}, "SK": {"S": "META"}}


def _s(item: Mapping[str, Any], name: str) -> str | None:
    return item.get(name, {}).get("S")


def _matches(binding: dict[str, Any], item: dict[str, Any]) -> bool:
    return bool(binding) and all(
        _s(binding, field) == _s(item, field)
        for field in ("user_id", "device_id", "binding_id")
    )


def _cas(item: dict[str, Any]) -> dict[str, Any]:
    if not item:
        return {"ConditionExpression": "attribute_not_exists(PK)"}
    return {
        "ConditionExpression": "binding_id=:binding",
        "ExpressionAttributeValues": {":binding": item["binding_id"]},
    }


def _devices(item: dict[str, Any]) -> list[str]:
    values = item.get("device_ids", {}).get("L", [])
    devices = [v["S"] for v in values if isinstance(v, dict) and "S" in v]
    if len(devices) > MAX_DEVICES or len(devices) != len(values):
        raise PushTokenConflict("Push registry is invalid")
    return devices


def _public(item: dict[str, Any], enabled: bool) -> dict[str, object]:
    return {
        "device_id": _s(item, "device_id"),
        "registered": enabled,
        "app_version": _s(item, "app_version"),
        "platform": _s(item, "platform"),
        "updated_at": _s(item, "updated_at"),
    }


def _conditional(error: Exception) -> bool:
    response = getattr(error, "response", {})
    return response.get("Error", {}).get("Code") in {
        "ConditionalCheckFailedException",
        "TransactionCanceledException",
    }
