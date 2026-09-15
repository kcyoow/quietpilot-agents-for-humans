"""Incremental Calendar consent, bound to the currently connected Gmail account."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from .connections import ConnectionFlowConflict, ConnectionInputError, _oauth_key

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"


class CalendarConnection:
    def __init__(self, table_name: str, client: Any, runtime: Any, callback_url: str):
        self.table_name = table_name
        self.client = client
        self.runtime = runtime
        self.callback_url = callback_url

    def _get(self, user_id: str, suffix: str) -> dict[str, Any]:
        return self.client.get_item(
            TableName=self.table_name,
            Key={"PK": {"S": f"USER#{user_id}"}, "SK": {"S": suffix}},
            ConsistentRead=True,
        ).get("Item", {})

    def _mail(self, user_id: str) -> tuple[str, str]:
        item = self._get(user_id, "CONNECTION#google")
        epoch = _text(item, "mail_connection_id")
        account = _text(item, "account_hash")
        if _text(item, "status") != "CONNECTED" or not epoch or not account:
            raise ConnectionInputError("Connect Gmail before enabling Calendar")
        return epoch, account

    def state(self, user_id: str) -> dict[str, object]:
        try:
            epoch, account = self._mail(user_id)
        except ConnectionInputError:
            return {"status": "DISCONNECTED", "granted_scopes": []}
        item = self._get(user_id, "CONNECTION#google-calendar")
        connected = (
            _text(item, "status") == "CONNECTED"
            and _text(item, "mail_connection_id") == epoch
            and _text(item, "account_hash") == account
            and CALENDAR_SCOPE
            in [entry.get("S") for entry in item.get("granted_scopes", {}).get("L", [])]
        )
        return {
            "status": "CONNECTED" if connected else "DISCONNECTED",
            "granted_scopes": [CALENDAR_SCOPE] if connected else [],
        }

    def authorize(self, user_id: str) -> dict[str, object]:
        epoch, account = self._mail(user_id)
        state = secrets.token_urlsafe(32)
        result = self.runtime.invoke(
            user_id,
            {
                "operation": "GOOGLE_CALENDAR_AUTHORIZE",
                "user_id": user_id,
                "callback_url": self.callback_url,
                "state": state,
            },
        )
        if result.get("status") == "TOKEN_AVAILABLE":
            self._confirm(user_id, epoch, account)
            return {}
        from .connections import _https_url, _session_uri

        url = _https_url(result.get("authorization_url"))
        session = _session_uri(result.get("session_uri"))
        if result.get("status") != "AUTHORIZATION_REQUIRED" or not url or not session:
            raise RuntimeError("Calendar authorization returned an invalid response")
        expires = int((datetime.now(UTC) + timedelta(minutes=10)).timestamp())
        self.client.put_item(
            TableName=self.table_name,
            Item={
                **_oauth_key(state),
                "entity_type": {"S": "oauth_flow"},
                "provider": {"S": "google"},
                "purpose": {"S": "calendar"},
                "user_id": {"S": user_id},
                "session_uri": {"S": session},
                "status": {"S": "PENDING"},
                "mail_connection_id": {"S": epoch},
                "account_hash": {"S": account},
                "expiresAt": {"N": str(expires)},
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
        return {"authorization_url": url}

    def validate_completion(self, user_id: str, state: str) -> tuple[str, str]:
        item = self.client.get_item(
            TableName=self.table_name,
            Key=_oauth_key(state),
            ConsistentRead=True,
        ).get("Item", {})
        if _text(item, "user_id") != user_id or _text(item, "purpose") != "calendar":
            raise ConnectionFlowConflict("Calendar authorization does not match")
        expires = item.get("expiresAt", {}).get("N")
        if (
            not isinstance(expires, str)
            or not expires.isdigit()
            or int(expires) <= int(datetime.now(UTC).timestamp())
            or _text(item, "status") not in {"PENDING", "COMPLETED"}
        ):
            raise ConnectionFlowConflict("Calendar authorization expired")
        epoch, account = self._mail(user_id)
        if (_text(item, "mail_connection_id"), _text(item, "account_hash")) != (
            epoch,
            account,
        ):
            raise ConnectionFlowConflict(
                "Gmail connection changed during authorization"
            )
        return epoch, account

    def complete(self, user_id: str, state: str) -> None:
        epoch, account = self.validate_completion(user_id, state)
        self._confirm(user_id, epoch, account)

    def _confirm(self, user_id: str, epoch: str, account: str) -> None:
        result = self.runtime.invoke(
            user_id, {"operation": "GOOGLE_CALENDAR_STATUS", "user_id": user_id}
        )
        if result.get("status") != "CONNECTED" or result.get("account_hash") != account:
            raise ConnectionFlowConflict(
                "Calendar must use the connected Gmail account"
            )
        self.client.transact_write_items(
            TransactItems=[
                {
                    "ConditionCheck": {
                        "TableName": self.table_name,
                        "Key": {
                            "PK": {"S": f"USER#{user_id}"},
                            "SK": {"S": "CONNECTION#google"},
                        },
                        "ConditionExpression": (
                            "#status=:connected AND mail_connection_id=:epoch "
                            "AND account_hash=:account"
                        ),
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": {
                            ":connected": {"S": "CONNECTED"},
                            ":epoch": {"S": epoch},
                            ":account": {"S": account},
                        },
                    }
                },
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": {
                            "PK": {"S": f"USER#{user_id}"},
                            "SK": {"S": "CONNECTION#google-calendar"},
                            "entity_type": {"S": "calendar_connection"},
                            "user_id": {"S": user_id},
                            "status": {"S": "CONNECTED"},
                            "mail_connection_id": {"S": epoch},
                            "account_hash": {"S": account},
                            "granted_scopes": {"L": [{"S": CALENDAR_SCOPE}]},
                            "verified_at": {"S": datetime.now(UTC).isoformat()},
                        },
                    }
                },
            ]
        )


def _text(item: dict[str, Any], name: str) -> str:
    value = item.get(name, {}).get("S")
    return value if isinstance(value, str) else ""
