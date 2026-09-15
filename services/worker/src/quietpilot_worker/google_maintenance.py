"""Bounded scheduled maintenance for connected Gmail accounts."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .google_jobs import BotoAgentRuntime, SqsContinuationQueue

MAX_CONNECTED_ACCOUNTS = 100
ALLOWED_MAINTENANCE_TASKS = frozenset(
    {"RENEW_GMAIL_WATCHES", "RECOVER_CONNECTION_SYNC"}
)


@dataclass(frozen=True, slots=True)
class ConnectedGoogleAccount:
    user_id: str
    history_id: str
    watch_expiration: str
    connection_id: str = ""


class MaintenanceStore(Protocol):
    def connected_accounts(self) -> list[ConnectedGoogleAccount]: ...

    def update_watch(self, user_id: str, *, expiration: str) -> None: ...

    def authorization_required(self, user_id: str, epoch: str) -> None: ...


class MaintenanceRuntime(Protocol):
    def invoke(
        self,
        user_id: str,
        operation: str,
        parameters: Mapping[str, object] | None = None,
    ) -> dict[str, object]: ...


class HistoryQueue(Protocol):
    def send_history(self, *, user_id: str, history_id: str) -> None: ...


class GoogleMaintenance:
    def __init__(
        self,
        store: MaintenanceStore,
        runtime: MaintenanceRuntime,
        queue: HistoryQueue,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._queue = queue

    def _invoke(
        self, account: ConnectedGoogleAccount, operation: str
    ) -> dict[str, object] | None:
        result = self._runtime.invoke(account.user_id, operation)
        if result == {
            "status": "AUTHORIZATION_REQUIRED",
            "error_code": "GOOGLE_AUTH_REQUIRED",
        }:
            self._store.authorization_required(account.user_id, account.connection_id)
            return None
        return result

    def run(self, task: str) -> dict[str, object]:
        if task not in ALLOWED_MAINTENANCE_TASKS:
            raise ValueError("Google maintenance task is not supported")
        accounts = self._store.connected_accounts()
        queued = 0
        renewed = 0
        for account in accounts:
            if task == "RENEW_GMAIL_WATCHES":
                result = self._invoke(account, "GOOGLE_RENEW_WATCH")
                if result is None:
                    continue
                if result.get("status") != "WATCH_RENEWED":
                    raise RuntimeError("AgentCore did not renew the Gmail watch")
                history_id = _history_id(result.get("history_id"))
                expiration = _expiration(result.get("watch_expiration"))
                self._store.update_watch(account.user_id, expiration=expiration)
                renewed += 1
                if int(history_id) > int(account.history_id):
                    self._queue.send_history(
                        user_id=account.user_id,
                        history_id=history_id,
                    )
                    queued += 1
                continue

            result = self._invoke(account, "GOOGLE_HISTORY_HEAD")
            if result is None:
                continue
            if result.get("status") != "HISTORY_HEAD":
                raise RuntimeError("AgentCore did not read the Gmail history head")
            history_id = _history_id(result.get("history_id"))
            if int(history_id) > int(account.history_id):
                self._queue.send_history(
                    user_id=account.user_id,
                    history_id=history_id,
                )
                queued += 1
        return {
            "status": "COMPLETED",
            "task": task,
            "account_count": len(accounts),
            "renewed_count": renewed,
            "queued_count": queued,
        }


class DynamoMaintenanceStore:
    def __init__(self, table_name: str, client: Any) -> None:
        self._table_name = table_name
        self._client = client

    @classmethod
    def from_environment(cls) -> DynamoMaintenanceStore:
        import boto3

        return cls(_required_environment("MAIN_TABLE_NAME"), boto3.client("dynamodb"))

    def connected_accounts(self) -> list[ConnectedGoogleAccount]:
        response = self._client.query(
            TableName=self._table_name,
            IndexName="GSI2",
            KeyConditionExpression="#pk=:pk",
            ExpressionAttributeNames={"#pk": "GSI2PK"},
            ExpressionAttributeValues={":pk": {"S": "CONNECTION#google#CONNECTED"}},
            ProjectionExpression="user_id,gmail_history_id,watch_expiration,mail_connection_id",
            Limit=MAX_CONNECTED_ACCOUNTS + 1,
        )
        items = response.get("Items")
        if not isinstance(items, list):
            raise TypeError("Connected Google account query is invalid")
        if len(items) > MAX_CONNECTED_ACCOUNTS or response.get("LastEvaluatedKey"):
            raise RuntimeError("Connected Google maintenance batch exceeds its bound")
        accounts: list[ConnectedGoogleAccount] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise TypeError("Connected Google account record is invalid")
            user_id = _dynamo_text(item, "user_id")
            history_id = _dynamo_text(item, "gmail_history_id")
            expiration = _dynamo_text(item, "watch_expiration", attribute_type="N")
            if (
                user_id is None
                or not user_id
                or len(user_id) > 128
                or history_id is None
                or not history_id.isdigit()
                or expiration is None
                or not expiration.isdigit()
            ):
                raise TypeError("Connected Google account fields are invalid")
            accounts.append(
                ConnectedGoogleAccount(
                    user_id=user_id,
                    history_id=history_id,
                    watch_expiration=expiration,
                    connection_id=_dynamo_text(item, "mail_connection_id") or "",
                )
            )
        return accounts

    def authorization_required(self, user_id: str, epoch: str) -> None:
        from .mail_jobs import DynamoMailJobStore

        DynamoMailJobStore(self._table_name, self._client).authorization_required(
            user_id, epoch
        )

    def update_watch(self, user_id: str, *, expiration: str) -> None:
        user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        renewed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._client.update_item(
            TableName=self._table_name,
            Key={
                "PK": {"S": f"USER#{user_id}"},
                "SK": {"S": "CONNECTION#google"},
            },
            UpdateExpression=(
                "SET watch_expiration=:expiration,GSI2SK=:gsi2_sk,"
                "watch_renewed_at=:renewed,last_checked_at=:renewed,"
                "version=if_not_exists(version,:zero)+:one"
            ),
            ConditionExpression="#status=:connected",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":connected": {"S": "CONNECTED"},
                ":expiration": {"N": expiration},
                ":gsi2_sk": {"S": f"{expiration}#{user_hash}"},
                ":renewed": {"S": renewed_at},
                ":zero": {"N": "0"},
                ":one": {"N": "1"},
            },
        )


def default_google_maintenance() -> GoogleMaintenance:
    return GoogleMaintenance(
        DynamoMaintenanceStore.from_environment(),
        BotoAgentRuntime.from_environment(),
        SqsContinuationQueue.from_environment(),
    )


def _history_id(value: object) -> str:
    if not isinstance(value, str) or not value.isdigit() or len(value) > 64:
        raise TypeError("Gmail history ID is invalid")
    return value


def _expiration(value: object) -> str:
    if not isinstance(value, str) or not value.isdigit() or len(value) > 16:
        raise TypeError("Gmail watch expiration is invalid")
    return value


def _dynamo_text(
    item: Mapping[str, Any],
    name: str,
    *,
    attribute_type: str = "S",
) -> str | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get(attribute_type), str):
        return value[attribute_type]
    return None


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value
