import json
import logging

from quietpilot_control_api.handlers import handle_request, handler


class _FakeConnectionService:
    def list_connections(self, user_id: str) -> list[dict[str, object]]:
        assert user_id == "tenant|non-uuid-subject"
        return []

    def scan(self, user_id: str, *, lookback_days: object) -> dict[str, object]:
        assert user_id == "tenant|non-uuid-subject"
        assert lookback_days == 7
        return {
            "provider": "google",
            "status": "SCANNING",
            "scan_progress": 5,
            "discovery_revision": 0,
        }


def _event(
    *,
    method: str = "GET",
    path: str = "/v1/connections",
    subject: str | None = "tenant|non-uuid-subject",
    body: dict[str, object] | None = None,
    idempotency_key: str | None = None,
    query: dict[str, str] | None = None,
) -> dict[str, object]:
    authorizer = {"jwt": {"claims": {"sub": subject}}} if subject is not None else {}
    return {
        "requestContext": {
            "requestId": "request-1",
            "routeKey": f"{method} {path}",
            "http": {"method": method, "path": path},
            "authorizer": authorizer,
        },
        "headers": {
            "authorization": "Bearer must-not-be-logged",
            **(
                {"Idempotency-Key": idempotency_key}
                if idempotency_key is not None
                else {}
            ),
        },
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }


def test_missing_jwt_subject_fails_closed() -> None:
    response = handler(_event(subject=None), object())
    assert response["statusCode"] == 401


def test_whitespace_only_jwt_subject_fails_closed() -> None:
    response = handler(_event(subject="   "), object())
    assert response["statusCode"] == 401


def test_authenticated_connection_inventory_uses_non_uuid_subject() -> None:
    response = handle_request(_event(), _FakeConnectionService())  # type: ignore[arg-type]
    assert response["statusCode"] == 200
    assert json.loads(str(response["body"])) == {"connections": []}


def test_authenticated_rescan_uses_authoritative_subject() -> None:
    response = handle_request(
        _event(
            method="POST",
            path="/v1/connections/google/scan",
            body={"lookback_days": 7},
        ),
        _FakeConnectionService(),  # type: ignore[arg-type]
    )

    assert response["statusCode"] == 202
    assert json.loads(str(response["body"]))["connection"] == {
        "provider": "google",
        "status": "SCANNING",
        "scan_progress": 5,
        "discovery_revision": 0,
    }


def test_body_user_id_is_rejected_even_when_it_matches() -> None:
    response = handler(
        _event(
            method="POST", path="/v1/cases", body={"user_id": "tenant|non-uuid-subject"}
        ),
        object(),
    )
    assert response["statusCode"] == 400
    assert "BODY_USER_ID_FORBIDDEN" in str(response["body"])


def test_unimplemented_authenticated_route_is_explicit() -> None:
    response = handler(_event(method="GET", path="/v1/policies"), object())
    assert response["statusCode"] == 501


def test_direct_case_uses_authoritative_subject_and_idempotency_header() -> None:
    class Workspace:
        def create_direct_case(
            self,
            user_id: str,
            *,
            prompt: object,
            idempotency_key: object,
        ) -> dict[str, object]:
            assert user_id == "tenant|non-uuid-subject"
            assert prompt == "이번 주 준비를 정리해 줘"
            assert idempotency_key == "request-direct-001"
            return {"case_id": "case-1", "status": "PREPARING"}

    response = handle_request(
        _event(
            method="POST",
            path="/v1/cases",
            body={"prompt": "이번 주 준비를 정리해 줘"},
            idempotency_key="request-direct-001",
        ),
        workspace_service=Workspace(),  # type: ignore[arg-type]
    )

    assert response["statusCode"] == 202
    assert json.loads(str(response["body"])) == {
        "case_id": "case-1",
        "status": "PREPARING",
    }


def test_live_suggestions_use_authoritative_subject() -> None:
    class Suggestions:
        def __init__(self) -> None:
            self.user_ids: list[str] = []

        def list_suggestions(self, user_id: str) -> dict[str, object]:
            self.user_ids.append(user_id)
            return {"suggestions": [], "next_cursor": None}

        def list_groups(self, user_id: str) -> dict[str, object]:
            raise AssertionError("wrong suggestion route")

    suggestions = Suggestions()
    response = handle_request(
        _event(method="GET", path="/v1/suggestions"),
        suggestion_service=suggestions,  # type: ignore[arg-type]
    )

    assert response["statusCode"] == 200
    assert suggestions.user_ids == ["tenant|non-uuid-subject"]


def test_suggestion_feedback_uses_authoritative_subject_and_version() -> None:
    class Suggestions:
        def submit_feedback(
            self,
            user_id: str,
            *,
            candidate_id: object,
            mode: object,
            expected_version: object,
        ) -> dict[str, object]:
            assert user_id == "tenant|non-uuid-subject"
            assert candidate_id == "candidate-1"
            assert mode == "HIDE_ONCE"
            assert expected_version == 3
            return {
                "affected_candidate_ids": ["candidate-1"],
                "candidate": None,
                "mode": "HIDE_ONCE",
                "rule_id": None,
            }

    response = handle_request(
        _event(
            method="POST",
            path="/v1/suggestions/candidate-1/feedback",
            body={"expected_version": 3, "mode": "HIDE_ONCE"},
            idempotency_key="feedback-request-001",
        ),
        suggestion_service=Suggestions(),  # type: ignore[arg-type]
    )

    assert response["statusCode"] == 200


def test_malformed_or_non_object_body_is_rejected() -> None:
    malformed = _event(method="POST", path="/v1/cases")
    malformed["body"] = "{not-json"
    assert handler(malformed, object())["statusCode"] == 400

    non_object = _event(method="POST", path="/v1/cases")
    non_object["body"] = json.dumps(["not", "an", "object"])
    assert handler(non_object, object())["statusCode"] == 400

    encoded = _event(method="POST", path="/v1/cases")
    encoded["body"] = "eyJwcm9tcHQiOiAieCJ9"
    encoded["isBase64Encoded"] = True
    assert handler(encoded, object())["statusCode"] == 400


def test_logs_are_allowlisted_and_exclude_identity_and_body(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="quietpilot_control_api.handlers"):
        handler(
            _event(
                method="POST",
                path="/v1/policies",
                body={"prompt": "private email body"},
            ),
            object(),
        )
    text = caplog.text
    assert "request-1" in text
    assert "private email body" not in text
    assert "must-not-be-logged" not in text
    assert "tenant|non-uuid-subject" not in text
