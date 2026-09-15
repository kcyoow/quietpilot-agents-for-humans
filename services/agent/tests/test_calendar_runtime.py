from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from quietpilot_agent.agentcore_runtime import run_agentcore_invocation
from quietpilot_agent.google_connector import (
    CALENDAR_EVENTS_SCOPE,
    GMAIL_READONLY_SCOPE,
    GoogleApiError,
    GoogleConnector,
)


class Identity:
    def __init__(self):
        self.calls = []

    def get_resource_oauth2_token(self, **payload):
        self.calls.append(payload)
        return {"accessToken": "fixture-secret-token"}


class Google:
    def __init__(self):
        self.calls = []
        self.event = None

    def request_json(self, method, url, **payload):
        self.calls.append((method, url))
        if url.endswith("/profile"):
            return {"emailAddress": "owner@example.test"}
        if "fields=kind" in url:
            return {"kind": "calendar#events"}
        if method == "POST":
            self.event = {**copy.deepcopy(payload["body"]), "status": "confirmed"}
            return self.event
        if self.event is None:
            raise GoogleApiError(404)
        return self.event


@pytest.fixture
def environment():
    identity, http = Identity(), Google()
    connector = GoogleConnector(
        identity, http, oauth_return_url="https://example.test/return"
    )
    payload = {
        "operation": "GOOGLE_CALENDAR_EXECUTE",
        "user_id": "owner",
        "operation_id": "approved-operation",
        "account_hash": hashlib.sha256(b"owner@example.test").hexdigest(),
        "parameters": {
            "summary": "예약 일정",
            "start": "2030-01-05T10:00:00+09:00",
            "end": "2030-01-05T11:00:00+09:00",
        },
    }
    return identity, http, connector, payload


def invoke(connector, payload):
    return run_agentcore_invocation(
        payload,
        SimpleNamespace(),
        google_connector=connector,
        workload_access_token="fixture-workload",
    )


def test_runtime_calendar_execution_uses_current_account_and_verified_readback(
    environment,
):
    identity, http, connector, payload = environment
    result = invoke(connector, payload)
    assert result["verified"] is True and result["status"] == "COMPLETED"
    assert [method for method, _ in http.calls] == ["GET", "GET", "POST", "GET"]
    assert identity.calls[0]["scopes"] == [GMAIL_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE]
    assert "fixture-secret-token" not in str(result)
    invoke(connector, payload)
    assert len([method for method, _ in http.calls if method == "POST"]) == 1


def test_calendar_authorization_cannot_execute_on_a_different_google_account(
    environment,
):
    _identity, http, connector, payload = environment
    payload["account_hash"] = "b" * 64
    result = invoke(connector, payload)
    assert result["error_code"] == "GOOGLE_ACCOUNT_CHANGED"
    assert http.calls == [
        ("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile")
    ]


@pytest.mark.parametrize(
    "change",
    [
        "attendees",
        "naive_time",
        "missing_approval_identity",
        "callback_on_execute",
        "arbitrary_operation",
    ],
)
def test_invalid_calendar_envelope_fails_before_identity_or_google(environment, change):
    identity, http, connector, payload = environment
    if change == "attendees":
        payload["parameters"]["attendees"] = ["someone@example.test"]
    elif change == "naive_time":
        payload["parameters"]["start"] = "2030-01-05T10:00:00"
    elif change == "missing_approval_identity":
        payload.pop("operation_id")
    elif change == "callback_on_execute":
        payload["callback_url"] = "https://example.test/return"
    else:
        payload["operation"] = "GOOGLE_CALENDAR_DELETE"
    with pytest.raises(ValidationError):
        invoke(connector, payload)
    assert identity.calls == [] and http.calls == []


def test_calendar_status_checks_access_without_reading_event_contents(environment):
    _identity, http, connector, _payload = environment
    result = invoke(
        connector, {"operation": "GOOGLE_CALENDAR_STATUS", "user_id": "owner"}
    )
    assert result["status"] == "CONNECTED"
    assert all(method == "GET" for method, _ in http.calls)
    assert http.calls[-1][1].endswith("maxResults=1&fields=kind")


def test_calendar_reconnect_requests_fresh_consent_without_replacing_mail_defaults(
    environment,
):
    identity, http, connector, _payload = environment
    result = invoke(
        connector,
        {
            "operation": "GOOGLE_CALENDAR_AUTHORIZE",
            "user_id": "owner",
            "callback_url": "https://example.test/return",
            "state": "s" * 43,
        },
    )
    assert result["status"] == "TOKEN_AVAILABLE"
    assert identity.calls[0]["forceAuthentication"] is True
    assert identity.calls[0]["scopes"] == [GMAIL_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE]
    assert http.calls == []
