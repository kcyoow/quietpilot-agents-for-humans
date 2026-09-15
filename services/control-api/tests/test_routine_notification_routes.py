import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quietpilot_control_api.handlers import handle_request
from quietpilot_control_api.notifications import PushTokenConflict
from quietpilot_control_api.routines import RoutineConflict, RoutineNotReady


def event(method, path, body=None, query=None, user="tenant|subject"):
    return {
        "requestContext": {
            "requestId": "test",
            "routeKey": f"{method} {path}",
            "http": {"method": method, "path": path},
            "authorizer": {"jwt": {"claims": {"sub": user}}},
        },
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": query,
    }


@pytest.mark.parametrize(
    "method,path,body,operation,kwargs",
    [
        ("GET", "/v1/routines", None, "list", {}),
        (
            "POST",
            "/v1/routines",
            {"case_id": "case-a", "expected_version": 4},
            "propose",
            {"case_id": "case-a", "expected_version": 4},
        ),
        (
            "POST",
            "/v1/routines/routine-a/activate",
            {"expected_version": 2},
            "activate",
            {"routine_id": "routine-a", "expected_version": 2},
        ),
        (
            "POST",
            "/v1/routines/routine-a/pause",
            {"expected_version": 3},
            "pause",
            {"routine_id": "routine-a", "expected_version": 3},
        ),
    ],
)
def test_routine_routes_preserve_authenticated_owner_and_reviewed_version(
    method, path, body, operation, kwargs
):
    service = SimpleNamespace(
        **{
            name: Mock(
                return_value={"routines": []}
                if name == "list"
                else {"routine": {"routine_id": "routine-a"}}
            )
            for name in ("list", "propose", "activate", "pause")
        }
    )
    result = handle_request(event(method, path, body), routine_service=service)
    assert result["statusCode"] == 200
    getattr(service, operation).assert_called_once_with("tenant|subject", **kwargs)


@pytest.mark.parametrize(
    "body",
    [
        {"case_id": "case-a", "expected_version": 1, "mode": "AUTO_EXECUTE"},
        {"expected_version": 1},
        {"case_id": "case-a", "expected_version": 1, "user_id": "other"},
    ],
)
def test_routine_request_cannot_add_authority_or_override_owner(body):
    service = SimpleNamespace(propose=Mock())
    result = handle_request(
        event("POST", "/v1/routines", body), routine_service=service
    )
    assert result["statusCode"] == 400
    service.propose.assert_not_called()


@pytest.mark.parametrize(
    "error,code",
    [
        (RoutineConflict(), "ROUTINE_VERSION_CONFLICT"),
        (RoutineNotReady(), "ROUTINE_NOT_READY"),
    ],
)
def test_routine_scope_conflict_is_reviewable_and_not_a_server_crash(error, code):
    service = SimpleNamespace(propose=Mock(side_effect=error))
    result = handle_request(
        event("POST", "/v1/routines", {"case_id": "case-a", "expected_version": 1}),
        routine_service=service,
    )
    assert result["statusCode"] == 409 and json.loads(result["body"])["error"] == code


def test_push_registration_and_removal_never_echo_the_token():
    service = SimpleNamespace(
        register=Mock(),
        unregister=Mock(),
        state=Mock(return_value={"device_id": "device-123", "registered": True}),
    )
    body = {
        "device_id": "device-123",
        "expo_push_token": "ExpoPushToken[SYNTHETIC_ONLY]",
        "app_version": "0.1.0",
        "platform": "android",
    }
    result = handle_request(
        event("PUT", "/v1/mobile/push-token", body), push_service=service
    )
    assert result["statusCode"] == 204 and result["body"] == ""
    service.register.assert_called_once_with("tenant|subject", **body)
    state = handle_request(
        event("GET", "/v1/mobile/push-token", query={"device_id": "device-123"}),
        push_service=service,
    )
    assert json.loads(state["body"]) == {"device_id": "device-123", "registered": True}
    result = handle_request(
        event("DELETE", "/v1/mobile/push-token", {"device_id": "device-123"}),
        push_service=service,
    )
    assert result["statusCode"] == 204 and result["body"] == ""
    service.unregister.assert_called_once_with("tenant|subject", device_id="device-123")


def test_push_registration_requires_auth_and_rejects_arbitrary_fields():
    service = SimpleNamespace(register=Mock())
    for request in [
        event("PUT", "/v1/mobile/push-token", {}, user=""),
        event("PUT", "/v1/mobile/push-token", {"user_id": "other"}),
        event(
            "PUT", "/v1/mobile/push-token", {"device_id": "device-123", "to": "other"}
        ),
    ]:
        assert handle_request(request, push_service=service)["statusCode"] in {400, 401}
    service.register.assert_not_called()


def test_push_conflict_is_exposed_without_private_error_detail():
    service = SimpleNamespace(
        unregister=Mock(side_effect=PushTokenConflict("PRIVATE_TOKEN"))
    )
    result = handle_request(
        event("DELETE", "/v1/mobile/push-token", {"device_id": "device-123"}),
        push_service=service,
    )
    assert result["statusCode"] == 409 and "PRIVATE_TOKEN" not in result["body"]
