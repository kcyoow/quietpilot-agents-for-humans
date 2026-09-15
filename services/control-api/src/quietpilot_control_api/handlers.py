"""Fail-closed HTTP API Lambda entrypoint for the local cloud foundation."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from .connections import (
    ConnectionFlowConflict,
    ConnectionFlowNotFound,
    ConnectionInputError,
    GoogleConnectionService,
    default_google_connection_service,
)
from .mail import (
    MailConflict,
    MailInputError,
    MailInterestService,
    default_mail_service,
)
from .notifications import PushTokenConflict, PushTokenInputError, PushTokenService
from .routines import (
    RoutineConflict,
    RoutineInputError,
    RoutineLimitError,
    RoutineNotFound,
    RoutineNotReady,
    RoutineService,
    default_routine_service,
)
from .suggestions import (
    SuggestionConflict,
    SuggestionInputError,
    SuggestionNotFound,
    SuggestionService,
    default_suggestion_service,
)
from .workspace import (
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceNotFound,
    WorkspaceNotReady,
    WorkspaceService,
    default_workspace_service,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_INVALID_BODY = object()
_CASE_PATH = re.compile(r"^/v1/cases/([^/]+)$")
_CASE_MESSAGES_PATH = re.compile(r"^/v1/cases/([^/]+)/messages$")
_CASE_DECISION_PATH = re.compile(r"^/v1/cases/([^/]+)/decision$")
_CASE_STOP_PATH = re.compile(r"^/v1/cases/([^/]+)/stop$")
_CASE_RETRY_PATH = re.compile(r"^/v1/cases/([^/]+)/retry$")
_SUGGESTION_FEEDBACK_PATH = re.compile(r"^/v1/suggestions/([^/]+)/feedback$")
_SUPPRESSION_UNDO_PATH = re.compile(r"^/v1/suppressions/([^/]+)/undo$")
_ROUTINE_STATE_PATH = re.compile(r"^/v1/routines/([^/]+)/(activate|pause)$")


def handler(event: Mapping[str, Any], context: object) -> dict[str, object]:
    """Handle authenticated foundation routes without logging request content."""

    del context
    return handle_request(event)


def handle_request(
    event: Mapping[str, Any],
    service: GoogleConnectionService | None = None,
    *,
    suggestion_service: SuggestionService | None = None,
    workspace_service: WorkspaceService | None = None,
    mail_service: MailInterestService | None = None,
    routine_service: RoutineService | None = None,
    push_service: PushTokenService | None = None,
) -> dict[str, object]:
    """Handle a request with injectable connection dependencies for tests."""

    request_context = _mapping(event.get("requestContext"))
    request_id = str(request_context.get("requestId", "unknown"))
    route_key = str(request_context.get("routeKey", "unknown"))
    user_id = _authoritative_user_id(request_context)

    if user_id is None:
        return _response(401, "AUTH_REQUIRED", request_id, route_key)

    body = _parse_body(
        event.get("body"),
        is_base64_encoded=event.get("isBase64Encoded") is True,
    )
    if body is _INVALID_BODY:
        return _response(400, "INVALID_JSON_BODY", request_id, route_key)
    if body is not None and not isinstance(body, dict):
        return _response(400, "BODY_OBJECT_REQUIRED", request_id, route_key)
    if isinstance(body, dict) and {"user_id", "userId"}.intersection(body):
        return _response(400, "BODY_USER_ID_FORBIDDEN", request_id, route_key)

    http = _mapping(request_context.get("http"))
    method = str(http.get("method", ""))
    path = str(http.get("path", ""))
    query = _mapping(event.get("queryStringParameters"))
    workspace = workspace_service
    try:
        routine_match = _ROUTINE_STATE_PATH.fullmatch(path)
        if path == "/v1/routines" or routine_match:
            routines = routine_service or default_routine_service()
            values = body or {}
            if method == "GET" and path == "/v1/routines":
                result = routines.list(user_id)
            elif method == "POST" and path == "/v1/routines":
                if set(values) != {"case_id", "expected_version"}:
                    raise RoutineInputError("Invalid routine proposal fields")
                result = routines.propose(
                    user_id,
                    case_id=values["case_id"],
                    expected_version=values["expected_version"],
                )
            elif method == "POST" and routine_match:
                if set(values) != {"expected_version"}:
                    raise RoutineInputError("Invalid routine state fields")
                operation = (
                    routines.activate
                    if routine_match[2] == "activate"
                    else routines.pause
                )
                result = operation(
                    user_id,
                    routine_id=routine_match[1],
                    expected_version=values["expected_version"],
                )
            else:
                return _response(404, "ROUTE_NOT_FOUND", request_id, route_key)
            _log_completion(request_id, route_key, 200)
            return _json_response(200, result)
        if path == "/v1/mobile/push-token":
            if push_service is None:
                import os

                import boto3

                push_service = PushTokenService(
                    os.environ["MAIN_TABLE_NAME"], boto3.client("dynamodb")
                )
            values = body or {}
            if method == "GET":
                if set(query) != {"device_id"}:
                    raise PushTokenInputError("Invalid push state fields")
                result = push_service.state(user_id, device_id=query["device_id"])
                _log_completion(request_id, route_key, 200)
                return _json_response(200, result)
            if method == "PUT":
                if set(values) != {
                    "expo_push_token",
                    "device_id",
                    "app_version",
                    "platform",
                }:
                    raise PushTokenInputError("Invalid push registration fields")
                push_service.register(user_id, **values)
            elif method == "DELETE":
                if set(values) != {"device_id"}:
                    raise PushTokenInputError("Invalid push removal fields")
                push_service.unregister(user_id, device_id=values["device_id"])
            else:
                return _response(404, "ROUTE_NOT_FOUND", request_id, route_key)
            _log_completion(request_id, route_key, 204)
            return {
                "statusCode": 204,
                "headers": {"cache-control": "no-store"},
                "body": "",
            }
        if path.startswith("/v1/mail/"):
            mail = mail_service or default_mail_service()
            values = body or {}
            if method == "GET" and path == "/v1/mail/interests":
                result = mail.state(user_id)
                status_code = 200
            elif method == "PUT" and path == "/v1/mail/interests":
                result = mail.save(
                    user_id,
                    tags=values.get("tags"),
                    description=values.get("description"),
                    expected_version=values.get("expected_version"),
                )
                status_code = 200
            elif method == "POST" and path == "/v1/mail/recommendations":
                result = mail.recommend(user_id)
                status_code = 202
            elif method == "POST" and path == "/v1/mail/scan":
                result = mail.scan(user_id)
                status_code = 202
            elif method == "GET" and path == "/v1/mail/results":
                result = mail.results(user_id, query.get("cursor"))
                status_code = 200
            else:
                return _response(501, "NOT_IMPLEMENTED", request_id, route_key)
            _log_completion(request_id, route_key, status_code)
            return _json_response(status_code, result)
        if path == "/v1/suggestions/convert" or path.startswith("/v1/cases"):
            workspace = workspace or default_workspace_service()
        feedback_match = _SUGGESTION_FEEDBACK_PATH.fullmatch(path)
        if method == "POST" and feedback_match:
            values = body or {}
            result = (
                suggestion_service or default_suggestion_service()
            ).submit_feedback(
                user_id,
                candidate_id=feedback_match.group(1),
                mode=values.get("mode"),
                expected_version=values.get("expected_version"),
            )
            response = _json_response(200, result)
            _log_completion(request_id, route_key, 200)
            return response
        undo_match = _SUPPRESSION_UNDO_PATH.fullmatch(path)
        if method == "POST" and undo_match:
            result = (
                suggestion_service or default_suggestion_service()
            ).undo_suppression(user_id, undo_match.group(1))
            response = _json_response(200, result)
            _log_completion(request_id, route_key, 200)
            return response
        if method == "GET" and path == "/v1/cases":
            assert workspace is not None
            result = workspace.list_cases(user_id, query.get("bucket"))
            response = _json_response(200, result)
            _log_completion(request_id, route_key, 200)
            return response
        if method == "POST" and path == "/v1/cases":
            assert workspace is not None
            values = body or {}
            result = workspace.create_direct_case(
                user_id,
                prompt=values.get("prompt"),
                idempotency_key=_header(event, "idempotency-key"),
            )
            response = _json_response(202, result)
            _log_completion(request_id, route_key, 202)
            return response
        if method == "POST" and path == "/v1/suggestions/convert":
            assert workspace is not None
            values = body or {}
            result = workspace.convert_candidates(
                user_id,
                candidate_ids=values.get("candidate_ids"),
                expected_versions=values.get("expected_versions"),
                idempotency_key=_header(event, "idempotency-key"),
            )
            response = _json_response(201, result)
            _log_completion(request_id, route_key, 201)
            return response
        case_match = _CASE_PATH.fullmatch(path)
        if method == "GET" and case_match:
            assert workspace is not None
            result = workspace.get_case(user_id, case_match.group(1))
            response = _json_response(200, result)
            _log_completion(request_id, route_key, 200)
            return response
        message_match = _CASE_MESSAGES_PATH.fullmatch(path)
        if method == "POST" and message_match:
            assert workspace is not None
            values = body or {}
            result = workspace.post_message(
                user_id,
                case_id=message_match.group(1),
                text=values.get("text"),
                expected_version=values.get("expected_version"),
                idempotency_key=_header(event, "idempotency-key"),
            )
            response = _json_response(202, result)
            _log_completion(request_id, route_key, 202)
            return response
        decision_match = _CASE_DECISION_PATH.fullmatch(path)
        retry_match = _CASE_RETRY_PATH.fullmatch(path)
        if method == "POST" and retry_match:
            assert workspace is not None
            result = workspace.retry(
                user_id,
                case_id=retry_match.group(1),
                expected_version=(body or {}).get("expected_version"),
            )
            _log_completion(request_id, route_key, 202)
            return _json_response(202, result)
        if method == "POST" and decision_match:
            assert workspace is not None
            values = body or {}
            result = workspace.decide(
                user_id,
                case_id=decision_match.group(1),
                decision=values.get("decision"),
                expected_version=values.get("expected_version"),
                **(
                    {
                        "plan_version": values.get("plan_version"),
                        "plan_hash": values.get("plan_hash"),
                        "grant_mode": values.get("grant_mode"),
                    }
                    if values.get("decision") == "APPROVE"
                    else {}
                ),
            )
            response = _json_response(200, result)
            _log_completion(request_id, route_key, 200)
            return response
        stop_match = _CASE_STOP_PATH.fullmatch(path)
        if method == "POST" and stop_match:
            assert workspace is not None
            values = body or {}
            result = workspace.decide(
                user_id,
                case_id=stop_match.group(1),
                decision="STOP",
                expected_version=values.get("expected_version"),
            )
            response = _json_response(200, result)
            _log_completion(request_id, route_key, 200)
            return response
        if method == "GET" and path == "/v1/suggestions":
            result = (
                suggestion_service or default_suggestion_service()
            ).list_suggestions(user_id)
            response = _json_response(200, result)
            _log_completion(request_id, route_key, 200)
            return response
        if method == "GET" and path == "/v1/suggestion-groups":
            result = (suggestion_service or default_suggestion_service()).list_groups(
                user_id
            )
            response = _json_response(200, result)
            _log_completion(request_id, route_key, 200)
            return response
        if method == "GET" and path == "/v1/connections":
            connections = (
                service or default_google_connection_service()
            ).list_connections(user_id)
            response = _json_response(200, {"connections": connections})
            _log_completion(request_id, route_key, 200)
            return response
        if method == "POST" and path == "/v1/connections/google/authorize":
            google_service = service or default_google_connection_service()
            values = body or {}
            result = (
                google_service.authorize(user_id, capability=values["capability"])
                if "capability" in values
                else google_service.authorize(user_id)
            )
            response = _json_response(200, result)
            _log_completion(request_id, route_key, 200)
            return response
        if method == "POST" and path == "/v1/connections/google/complete":
            values = body or {}
            connection = (service or default_google_connection_service()).complete(
                user_id,
                code=values.get("code"),
            )
            response = _json_response(202, {"connection": connection})
            _log_completion(request_id, route_key, 202)
            return response
        if method == "POST" and path == "/v1/connections/google/scan":
            values = body or {}
            connection = (service or default_google_connection_service()).scan(
                user_id,
                lookback_days=values.get("lookback_days"),
            )
            response = _json_response(202, {"connection": connection})
            _log_completion(request_id, route_key, 202)
            return response
        if method == "DELETE" and path == "/v1/connections/google":
            connection = (service or default_google_connection_service()).disconnect(
                user_id
            )
            response = _json_response(202, {"connection": connection})
            _log_completion(request_id, route_key, 202)
            return response
    except RoutineInputError:
        return _response(400, "ROUTINE_INPUT_INVALID", request_id, route_key)
    except RoutineNotFound:
        return _response(404, "ROUTINE_NOT_FOUND", request_id, route_key)
    except RoutineConflict:
        return _response(409, "ROUTINE_VERSION_CONFLICT", request_id, route_key)
    except RoutineNotReady:
        return _response(409, "ROUTINE_NOT_READY", request_id, route_key)
    except RoutineLimitError:
        return _response(409, "ROUTINE_LIMIT_EXCEEDED", request_id, route_key)
    except PushTokenInputError:
        return _response(400, "PUSH_INPUT_INVALID", request_id, route_key)
    except PushTokenConflict:
        return _response(409, "PUSH_REGISTRATION_CONFLICT", request_id, route_key)
    except MailInputError:
        return _response(400, "INVALID_MAIL_INPUT", request_id, route_key)
    except MailConflict:
        return _response(409, "MAIL_VERSION_CONFLICT", request_id, route_key)
    except ConnectionInputError:
        return _response(400, "INVALID_CONNECTION_INPUT", request_id, route_key)
    except ConnectionFlowNotFound:
        return _response(404, "CONNECTION_FLOW_NOT_FOUND", request_id, route_key)
    except ConnectionFlowConflict:
        return _response(409, "CONNECTION_FLOW_CONFLICT", request_id, route_key)
    except WorkspaceInputError:
        return _response(400, "INVALID_WORKSPACE_INPUT", request_id, route_key)
    except WorkspaceNotFound:
        return _response(404, "WORKSPACE_RECORD_NOT_FOUND", request_id, route_key)
    except WorkspaceConflict:
        return _response(409, "WORKSPACE_VERSION_CONFLICT", request_id, route_key)
    except WorkspaceNotReady:
        return _response(501, "ACTION_EXECUTION_NOT_READY", request_id, route_key)
    except SuggestionInputError:
        return _response(400, "INVALID_SUGGESTION_FEEDBACK", request_id, route_key)
    except SuggestionNotFound:
        return _response(404, "SUGGESTION_RULE_NOT_FOUND", request_id, route_key)
    except SuggestionConflict:
        return _response(409, "SUGGESTION_VERSION_CONFLICT", request_id, route_key)
    except Exception:
        logger.exception(
            json.dumps(
                {
                    "event": "request_failed",
                    "request_id": request_id,
                    "route_key": route_key,
                },
                separators=(",", ":"),
            )
        )
        error_code = (
            "CONNECTION_SERVICE_UNAVAILABLE"
            if path.startswith("/v1/connections")
            else "SERVICE_UNAVAILABLE"
        )
        return _response(502, error_code, request_id, route_key)

    return _response(501, "NOT_IMPLEMENTED", request_id, route_key)


def _authoritative_user_id(request_context: Mapping[str, Any]) -> str | None:
    authorizer = _mapping(request_context.get("authorizer"))
    jwt = _mapping(authorizer.get("jwt"))
    claims = _mapping(jwt.get("claims"))
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 256:
        return None
    return subject


def _parse_body(value: object, *, is_base64_encoded: bool) -> object:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or is_base64_encoded:
        return _INVALID_BODY
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return _INVALID_BODY


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _header(event: Mapping[str, Any], name: str) -> str | None:
    headers = _mapping(event.get("headers"))
    for key, value in headers.items():
        if str(key).casefold() == name.casefold() and isinstance(value, str):
            return value
    return None


def _response(
    status_code: int,
    error_code: str,
    request_id: str,
    route_key: str,
) -> dict[str, object]:
    _log_completion(request_id, route_key, status_code, error_code)
    return _json_response(
        status_code,
        {
            "error": error_code,
            "request_id": request_id,
        },
    )


def _json_response(status_code: int, body: dict[str, object]) -> dict[str, object]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
    }


def _log_completion(
    request_id: str,
    route_key: str,
    status_code: int,
    error_code: str | None = None,
) -> None:
    record: dict[str, object] = {
        "event": "request_completed",
        "request_id": request_id,
        "route_key": route_key,
        "status": status_code,
    }
    if error_code is not None:
        record["error_code"] = error_code
    logger.info(json.dumps(record, separators=(",", ":")))
