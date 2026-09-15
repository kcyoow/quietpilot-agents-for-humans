from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from quietpilot_agent import agentcore_runtime
from quietpilot_agent.google_connector import (
    GMAIL_READONLY_SCOPE,
    GoogleApiError,
    GoogleAuthorizationRequired,
    GoogleConnector,
)


def _entrypoint(monkeypatch, dispatcher):
    class App:
        def entrypoint(self, handler):
            return handler

    runtime = ModuleType("bedrock_agentcore.runtime")
    runtime.BedrockAgentCoreApp = App
    package = ModuleType("bedrock_agentcore")
    package.runtime = runtime
    monkeypatch.setitem(sys.modules, "bedrock_agentcore", package)
    monkeypatch.setitem(sys.modules, "bedrock_agentcore.runtime", runtime)
    monkeypatch.setattr(agentcore_runtime, "run_agentcore_invocation", dispatcher)
    monkeypatch.setattr(sys, "path", list(sys.path))
    return runpy.run_path(str(Path(__file__).parents[1] / "main.py"))["invoke"]


class _Identity:
    def get_resource_oauth2_token(self, **values: object) -> dict[str, str]:
        return {
            "authorizationUrl": "https://accounts.example.test/consent?private=value",
            "sessionUri": "private-authorization-session",
        }


class _NoMailOrModel:
    def request_json(self, *args: object, **kwargs: object):
        raise AssertionError("authorization failure must happen before mail reads")

    def create(self, *args: object, **kwargs: object):
        raise AssertionError("authorization failure must happen before model creation")


@pytest.mark.parametrize(
    ("operation", "fields"),
    [
        ("GOOGLE_MAIL_SETUP", {}),
        ("GOOGLE_INTEREST_TAGS", {}),
        ("GOOGLE_INTEREST_SCAN", {}),
        ("GOOGLE_INTEREST_SCAN_PAGE", {"page_token": "next-page"}),
        ("GOOGLE_INTEREST_HISTORY_SYNC", {"start_history_id": "42"}),
    ],
)
def test_mail_entrypoints_return_safe_reconnect_state_before_reading_mail(
    monkeypatch, operation: str, fields: dict[str, object]
) -> None:
    actual_dispatch = agentcore_runtime.run_agentcore_invocation
    connector = GoogleConnector(
        identity=_Identity(),
        google=_NoMailOrModel(),
        oauth_return_url="https://example.test/oauth-return",
    )

    def dispatch(payload, context):
        return actual_dispatch(
            payload,
            context,
            google_connector=connector,
            workload_access_token="offline-workload-token",
            model_factory=_NoMailOrModel(),
        )

    payload = {"operation": operation, "user_id": "owner", **fields}
    if operation in {
        "GOOGLE_INTEREST_SCAN",
        "GOOGLE_INTEREST_SCAN_PAGE",
        "GOOGLE_INTEREST_HISTORY_SYNC",
    }:
        payload["interest_profile"] = {
            "revision": 1,
            "tags": ["학교"],
            "description": "",
        }
    result = _entrypoint(monkeypatch, dispatch)(payload, SimpleNamespace())

    assert result == {
        "status": "AUTHORIZATION_REQUIRED",
        "error_code": "GOOGLE_AUTH_REQUIRED",
    }


def test_entrypoint_auth_failure_never_copies_exception_text(monkeypatch) -> None:
    def dispatch(payload, context):
        raise GoogleAuthorizationRequired("private token, URL or connector content")

    result = _entrypoint(monkeypatch, dispatch)({}, SimpleNamespace())
    assert result == {
        "status": "AUTHORIZATION_REQUIRED",
        "error_code": "GOOGLE_AUTH_REQUIRED",
    }


def test_normal_google_authorize_keeps_its_consent_response(monkeypatch) -> None:
    actual_dispatch = agentcore_runtime.run_agentcore_invocation
    connector = GoogleConnector(identity=_Identity(), google=_NoMailOrModel())

    def dispatch(payload, context):
        return actual_dispatch(
            payload,
            context,
            google_connector=connector,
            workload_access_token="offline-workload-token",
        )

    result = _entrypoint(monkeypatch, dispatch)(
        {
            "operation": "GOOGLE_AUTHORIZE",
            "user_id": "owner",
            "callback_url": "https://example.test/oauth-return",
            "state": "s" * 32,
        },
        SimpleNamespace(),
    )
    assert result == {
        "status": "AUTHORIZATION_REQUIRED",
        "authorization_url": "https://accounts.example.test/consent?private=value",
        "session_uri": "private-authorization-session",
        "scopes": [GMAIL_READONLY_SCOPE],
    }


@pytest.mark.parametrize(
    "error",
    [GoogleApiError(401), RuntimeError("other error"), ValueError("invalid input")],
)
def test_entrypoint_preserves_other_error_handling(
    monkeypatch, error: Exception
) -> None:
    def dispatch(payload, context):
        raise error

    with pytest.raises(type(error)) as raised:
        _entrypoint(monkeypatch, dispatch)({}, SimpleNamespace())
    assert raised.value is error
