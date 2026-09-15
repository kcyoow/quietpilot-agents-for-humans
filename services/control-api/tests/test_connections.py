from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from quietpilot_control_api.connections import (
    GMAIL_READONLY_SCOPE,
    ConnectionFlowConflict,
    DynamoConnectionStore,
    GoogleConnectionService,
    _connection_from_item,
)


class _Store:
    def __init__(self) -> None:
        self.connection: dict[str, object] | None = None
        self.flow: dict[str, object] | None = None
        self.state: str | None = None
        self.return_code: str | None = None
        self.oauth_return: dict[str, object] | None = None

    def get_connection(self, user_id: str) -> dict[str, object] | None:
        del user_id
        return self.connection

    def put_connection(
        self,
        user_id: str,
        *,
        status: str,
        granted_scopes: list[str],
        scan_progress: int,
        discovery_revision: int,
        lookback_days: int = 7,
        scan_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, object]:
        del user_id
        if (
            status == "CONNECTING"
            and self.connection is not None
            and self.connection.get("status") == "REVOKING"
        ):
            raise ConnectionFlowConflict("Google connection state changed")
        self.connection = {
            "provider": "google",
            "status": status,
            "granted_scopes": granted_scopes,
            "lookback_days": lookback_days,
            "scan_progress": scan_progress,
            "discovery_revision": discovery_revision,
            "error_code": error_code,
        }
        if scan_id is not None:
            self.connection["_scan_id"] = scan_id
        return self.connection

    def put_oauth_flow(
        self,
        user_id: str,
        *,
        state: str,
        session_uri: str,
        expires_at: int,
    ) -> None:
        self.state = state
        self.flow = {
            "user_id": user_id,
            "session_uri": session_uri,
            "expires_at": expires_at,
            "status": "PENDING",
        }

    def get_oauth_flow(self, state: str) -> dict[str, object] | None:
        return self.flow if state == self.state else None

    def complete_oauth_flow(self, state: str) -> None:
        assert state == self.state
        assert self.flow is not None
        self.flow["status"] = "COMPLETED"

    def get_oauth_return(self, code: str) -> dict[str, object] | None:
        return self.oauth_return if code == self.return_code else None

    def complete_oauth_return(self, code: str) -> None:
        assert code == self.return_code
        assert self.oauth_return is not None
        self.oauth_return["status"] = "COMPLETED"

    def issue_return_code(self, code: str) -> None:
        assert self.flow is not None
        assert self.state is not None
        self.return_code = code
        self.oauth_return = {**self.flow, "state": self.state}


class _Runtime:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    def invoke(self, user_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((user_id, payload))
        return self.result


class _Identity:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, user_id: str, session_uri: str) -> None:
        self.calls.append((user_id, session_uri))


class _Queue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.payloads: list[dict[str, object]] = []

    def send(
        self,
        *,
        user_id: str,
        event_type: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.calls.append((user_id, event_type))
        self.payloads.append(dict(payload or {}))


class _Mail:
    def __init__(self) -> None:
        self.scans: list[str] = []

    def state(self, user_id: str) -> dict[str, object]:
        return {"profile": {"tags": ["학교"], "description": ""}}

    def scan(self, user_id: str) -> None:
        self.scans.append(user_id)


def _service(
    runtime_result: dict[str, object],
) -> tuple[GoogleConnectionService, _Store, _Identity, _Queue]:
    store = _Store()
    identity = _Identity()
    queue = _Queue()
    return (
        GoogleConnectionService(
            store=store,
            runtime=_Runtime(runtime_result),
            identity=identity,
            queue=queue,
            callback_url="https://api.example.com/oauth/google/callback",
            mail=_Mail(),
        ),
        store,
        identity,
        queue,
    )


def test_authorize_persists_owner_bound_session_without_exposing_session_uri() -> None:
    session_uri = "urn:ietf:params:oauth:request_uri:session-1"
    service, store, _, queue = _service(
        {
            "status": "AUTHORIZATION_REQUIRED",
            "authorization_url": "https://accounts.google.com/o/oauth2/auth?x=1",
            "session_uri": session_uri,
        }
    )

    result = service.authorize("cognito-subject")

    assert result["authorization_url"].startswith("https://accounts.google.com/")
    assert "session_uri" not in result
    assert store.flow is not None
    assert store.flow["user_id"] == "cognito-subject"
    assert store.flow["session_uri"] == session_uri
    assert queue.calls == []


def test_complete_requires_same_owner_and_enqueues_mail_setup_only() -> None:
    session_uri = "urn:ietf:params:oauth:request_uri:session-1"
    service, store, identity, queue = _service(
        {
            "status": "AUTHORIZATION_REQUIRED",
            "authorization_url": "https://accounts.google.com/o/oauth2/auth?x=1",
            "session_uri": session_uri,
        }
    )
    service.authorize("cognito-subject")
    assert store.state is not None
    store.issue_return_code("c" * 43)

    connection = service.complete(
        "cognito-subject",
        code="c" * 43,
    )

    assert connection["status"] == "CONNECTING"
    assert connection["granted_scopes"] == [GMAIL_READONLY_SCOPE]
    assert connection["discovery_revision"] == 0
    assert identity.calls == [("cognito-subject", session_uri)]
    assert queue.calls == [("cognito-subject", "MAIL_SETUP_REQUESTED")]
    assert queue.payloads == [{"scan_id": store.connection["_scan_id"]}]


def test_authorize_does_not_call_runtime_during_revocation() -> None:
    service, store, _, queue = _service({"status": "TOKEN_AVAILABLE"})
    store.connection = {"status": "REVOKING"}

    with pytest.raises(ConnectionFlowConflict, match="disconnection"):
        service.authorize("owner")

    assert service.runtime.calls == []
    assert queue.calls == []


def test_pending_oauth_complete_does_not_call_identity_during_revocation() -> None:
    service, store, identity, queue = _service(
        {
            "status": "AUTHORIZATION_REQUIRED",
            "authorization_url": "https://accounts.google.com/o/oauth2/auth?x=1",
            "session_uri": "urn:ietf:params:oauth:request_uri:session-1",
        }
    )
    service.authorize("owner")
    store.issue_return_code("c" * 43)
    store.connection = {"status": "REVOKING"}

    with pytest.raises(ConnectionFlowConflict, match="disconnection"):
        service.complete("owner", code="c" * 43)

    assert identity.calls == []
    assert queue.calls == []
    assert store.oauth_return["status"] == "PENDING"


@pytest.mark.parametrize("operation", ["authorize", "complete"])
def test_oauth_completion_cannot_overwrite_an_intervening_revocation(
    operation: str,
) -> None:
    service, store, identity, queue = _service(
        {
            "status": "AUTHORIZATION_REQUIRED",
            "authorization_url": "https://accounts.google.com/o/oauth2/auth?x=1",
            "session_uri": "urn:ietf:params:oauth:request_uri:session-1",
        }
    )
    service.authorize("owner")
    store.issue_return_code("c" * 43)

    def revoke_during_provider_call(*args: object) -> dict[str, str]:
        store.connection = {"status": "REVOKING"}
        return {"status": "TOKEN_AVAILABLE"}

    if operation == "authorize":
        service.runtime.invoke = revoke_during_provider_call
        invoke = lambda: service.authorize("owner")
    else:
        identity.complete = revoke_during_provider_call
        invoke = lambda: service.complete("owner", code="c" * 43)

    with pytest.raises(ConnectionFlowConflict, match="state changed"):
        invoke()

    assert store.connection["status"] == "REVOKING"
    assert queue.calls == []


def test_dynamo_connecting_write_condition_preserves_a_concurrent_revocation() -> None:
    class ConditionalFailure(Exception):
        pass

    calls: list[dict[str, object]] = []

    def update_item(**request: object) -> dict[str, object]:
        calls.append(request)
        raise ConditionalFailure()

    client = SimpleNamespace(
        update_item=update_item,
        exceptions=SimpleNamespace(ConditionalCheckFailedException=ConditionalFailure),
    )
    store = DynamoConnectionStore("offline-table", client)
    with pytest.raises(ConnectionFlowConflict, match="state changed"):
        store.put_connection(
            "owner",
            status="CONNECTING",
            granted_scopes=[],
            scan_progress=0,
            discovery_revision=0,
        )

    assert (
        calls[0]["ConditionExpression"]
        == "attribute_not_exists(#status) OR #status<>:revoking"
    )
    assert calls[0]["ExpressionAttributeValues"][":revoking"] == {"S": "REVOKING"}


def test_each_pending_consent_transition_rotates_the_mail_connection_generation() -> (
    None
):
    calls: list[dict[str, object]] = []

    def update_item(**request: object) -> dict[str, object]:
        calls.append(request)
        return {"Attributes": {"status": {"S": "CONNECTING"}}}

    store = DynamoConnectionStore(
        "offline-table", SimpleNamespace(update_item=update_item)
    )
    for _ in range(2):
        store.put_connection(
            "owner",
            status="CONNECTING",
            granted_scopes=[],
            scan_progress=0,
            discovery_revision=0,
        )

    epochs = [call["ExpressionAttributeValues"][":mail_epoch"]["S"] for call in calls]
    assert len(set(epochs)) == 2
    assert all(len(epoch) == 32 for epoch in epochs)
    assert all(
        "mail_connection_id=:mail_epoch" in call["UpdateExpression"] for call in calls
    )


def test_connected_account_rescan_uses_persisted_mail_interest_service() -> None:
    service, store, _, queue = _service({"status": "TOKEN_AVAILABLE"})
    store.connection = {
        "provider": "google",
        "status": "CONNECTED",
        "granted_scopes": [GMAIL_READONLY_SCOPE],
        "lookback_days": 7,
        "scan_progress": 100,
        "discovery_revision": 0,
    }

    connection = service.scan("cognito-subject", lookback_days=7)

    assert connection["status"] == "CONNECTED"
    assert connection["scan_progress"] == 100
    assert connection["discovery_revision"] == 0
    assert queue.calls == []
    assert service.mail.scans == ["cognito-subject"]


def test_incomplete_scan_error_can_request_a_fresh_scan() -> None:
    service, store, _, queue = _service({"status": "TOKEN_AVAILABLE"})
    store.connection = {
        "provider": "google",
        "status": "ERROR",
        "granted_scopes": [GMAIL_READONLY_SCOPE],
        "error_code": "DISCOVERY_INCOMPLETE",
        "scan_progress": 99,
        "discovery_revision": 0,
    }

    connection = service.scan("cognito-subject", lookback_days=7)

    assert connection["status"] == "CONNECTING"
    assert connection["error_code"] is None
    assert queue.calls == [("cognito-subject", "MAIL_SETUP_REQUESTED")]


def test_legacy_scanning_connection_recovers_through_mail_setup() -> None:
    service, store, identity, queue = _service({"status": "TOKEN_AVAILABLE"})
    store.connection = {"status": "SCANNING", "granted_scopes": [GMAIL_READONLY_SCOPE]}
    service.mail.state = lambda user: {"profile": {"tags": [], "description": ""}}

    result = service.scan("owner", lookback_days=7)

    assert result["status"] == "CONNECTING"
    assert queue.calls == [("owner", "MAIL_SETUP_REQUESTED")]
    assert queue.payloads == [{"scan_id": store.connection["_scan_id"]}]
    assert service.runtime.calls == []
    assert identity.calls == []
    assert service.mail.scans == []


@pytest.mark.parametrize("lookback_days", [None, True, 0, 31, "7"])
def test_rescan_rejects_invalid_lookback_without_queueing(
    lookback_days: object,
) -> None:
    service, store, _, queue = _service({"status": "TOKEN_AVAILABLE"})
    store.connection = {
        "provider": "google",
        "status": "CONNECTED",
        "granted_scopes": [GMAIL_READONLY_SCOPE],
    }

    with pytest.raises(ValueError, match="lookback_days"):
        service.scan("cognito-subject", lookback_days=lookback_days)

    assert queue.calls == []


def test_complete_rejects_a_different_authenticated_user() -> None:
    service, store, identity, queue = _service({"status": "TOKEN_AVAILABLE"})
    store.return_code = "c" * 43
    store.oauth_return = {
        "user_id": "owner",
        "state": "a" * 43,
        "session_uri": "urn:ietf:params:oauth:request_uri:session-1",
        "expires_at": int(datetime.now(UTC).timestamp()) + 60,
        "status": "PENDING",
    }

    with pytest.raises(ConnectionFlowConflict, match="another user"):
        service.complete(
            "attacker",
            code=store.return_code,
        )

    assert identity.calls == []
    assert queue.calls == []


def test_existing_token_skips_consent_and_enqueues_mail_setup_only() -> None:
    service, store, _, queue = _service({"status": "TOKEN_AVAILABLE"})

    result = service.authorize("cognito-subject")

    assert "authorization_url" not in result
    assert result["connection"]["status"] == "CONNECTING"
    assert queue.calls == [("cognito-subject", "MAIL_SETUP_REQUESTED")]
    assert queue.payloads == [{"scan_id": store.connection["_scan_id"]}]


def test_disconnect_is_async_and_revocation_is_explicit() -> None:
    service, _, _, queue = _service({"status": "TOKEN_AVAILABLE"})

    result = service.disconnect("cognito-subject")

    assert result["status"] == "REVOKING"
    assert queue.calls == [("cognito-subject", "GOOGLE_CONNECTION_REVOKED")]


def test_auth_required_legacy_scan_returns_reconnect_state_without_queueing() -> None:
    service, store, _, queue = _service({"status": "TOKEN_AVAILABLE"})
    store.connection = {
        "provider": "google",
        "status": "ERROR",
        "error_code": "GOOGLE_AUTH_REQUIRED",
        "granted_scopes": [GMAIL_READONLY_SCOPE],
    }
    result = service.scan("cognito-subject", lookback_days=7)
    assert result["error_code"] == "GOOGLE_AUTH_REQUIRED"
    assert not queue.calls and not service.mail.scans


def test_connection_readback_exposes_watch_health_without_private_cursor() -> None:
    expiration = int(datetime(2026, 9, 6, tzinfo=UTC).timestamp() * 1000)
    connection = _connection_from_item(
        {
            "provider": {"S": "google"},
            "label": {"S": "Google"},
            "status": {"S": "CONNECTED"},
            "granted_scopes": {"L": [{"S": GMAIL_READONLY_SCOPE}]},
            "lookback_days": {"N": "7"},
            "scan_progress": {"N": "100"},
            "discovery_revision": {"N": "1"},
            "last_checked_at": {"S": "2026-08-30T00:05:00Z"},
            "last_sync_mode": {"S": "INCREMENTAL"},
            "watch_renewed_at": {"S": "2026-08-30T00:00:00Z"},
            "watch_expiration": {"N": str(expiration)},
            "gmail_history_id": {"S": "private-cursor"},
            "account_hash": {"S": "private-account-hash"},
            "version": {"N": "9"},
        }
    )

    assert connection["watch_renewed_at"] == "2026-08-30T00:00:00Z"
    assert connection["next_renewal_due_at"] == "2026-08-31T00:00:00Z"
    assert connection["watch_expires_at"] == "2026-09-06T00:00:00Z"
    assert connection["last_sync_mode"] == "INCREMENTAL"
    assert connection["discovery_revision"] == 1
    assert "gmail_history_id" not in connection
    assert "account_hash" not in connection
