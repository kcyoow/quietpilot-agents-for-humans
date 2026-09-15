import base64
import hashlib
import json
import logging

from quietpilot_ingress.handlers import handle_request


class _HistoryReceiver:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls: list[dict[str, str]] = []

    def receive(self, **values: str) -> bool:
        self.calls.append(values)
        return self.result


def _event(
    *,
    state: object = "a" * 43,
    session_id: object = "urn:ietf:params:oauth:request_uri:session-1",
) -> dict[str, object]:
    return {
        "requestContext": {
            "requestId": "request-1",
            "http": {"method": "GET", "path": "/oauth/google/callback"},
        },
        "queryStringParameters": {"state": state, "session_id": session_id},
    }


def test_valid_callback_redirects_to_app_without_tokens() -> None:
    response = handle_request(_event(), lambda state, session: "c" * 43)

    assert response["statusCode"] == 302
    location = response["headers"]["location"]
    assert location.startswith("quietpilot://oauth-return?provider=google")
    assert "code=" + "c" * 43 in location
    assert "session_id" not in location
    assert "state=" not in location
    assert response["headers"]["cache-control"] == "no-store"


def test_invalid_callback_renders_safe_retry_message() -> None:
    response = handle_request(_event(state="short"), lambda state, session: "unused")

    assert response["statusCode"] == 400
    assert "다시 연결" in response["body"]
    assert "short" not in response["body"]


def test_callback_logs_do_not_include_state_or_session(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="quietpilot_ingress.handlers"):
        handle_request(_event(), lambda state, session: "c" * 43)

    assert "request-1" in caplog.text
    assert "session-1" not in caplog.text
    assert "a" * 43 not in caplog.text


def _pubsub_event(
    *,
    claim_email: str = "quietpilot-push@example.iam.gserviceaccount.com",
    data: dict[str, object] | None = None,
    encoded_data: str | None = None,
) -> dict[str, object]:
    payload = data or {
        "emailAddress": "private@example.com",
        "historyId": "42",
    }
    encoded = (
        encoded_data
        if encoded_data is not None
        else base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )
    return {
        "requestContext": {
            "requestId": "request-2",
            "http": {"method": "POST", "path": "/webhooks/google/pubsub"},
            "authorizer": {
                "jwt": {
                    "claims": {
                        "email": claim_email,
                        "email_verified": "true",
                    }
                }
            },
        },
        "body": json.dumps({"message": {"messageId": "pubsub-1", "data": encoded}}),
        "isBase64Encoded": False,
    }


def test_authenticated_pubsub_maps_account_hash_and_queues_history() -> None:
    receiver = _HistoryReceiver()

    response = handle_request(
        _pubsub_event(),
        lambda state, session: "unused",
        google_history_receiver=receiver,
        expected_pubsub_email="quietpilot-push@example.iam.gserviceaccount.com",
    )

    assert response["statusCode"] == 204
    assert receiver.calls == [
        {
            "account_hash": hashlib.sha256(b"private@example.com").hexdigest(),
            "history_id": "42",
            "message_id": "pubsub-1",
        }
    ]
    assert "private@example.com" not in json.dumps(response)


def test_authenticated_pubsub_normalizes_numeric_gmail_history_id() -> None:
    receiver = _HistoryReceiver()

    response = handle_request(
        _pubsub_event(data={"emailAddress": "private@example.com", "historyId": 42}),
        lambda state, session: "unused",
        google_history_receiver=receiver,
        expected_pubsub_email="quietpilot-push@example.iam.gserviceaccount.com",
    )

    assert response["statusCode"] == 204
    assert receiver.calls[0]["history_id"] == "42"


def test_pubsub_rejects_invalid_base64url_without_queueing(caplog) -> None:
    receiver = _HistoryReceiver()

    with caplog.at_level(logging.INFO, logger="quietpilot_ingress.handlers"):
        response = handle_request(
            _pubsub_event(encoded_data="***"),
            lambda state, session: "unused",
            google_history_receiver=receiver,
            expected_pubsub_email="quietpilot-push@example.iam.gserviceaccount.com",
        )

    assert response["statusCode"] == 400
    assert receiver.calls == []
    assert "DATA_ENCODING_INVALID" in caplog.text


def test_pubsub_rejects_another_service_account_before_queueing() -> None:
    receiver = _HistoryReceiver()

    response = handle_request(
        _pubsub_event(claim_email="another@example.iam.gserviceaccount.com"),
        lambda state, session: "unused",
        google_history_receiver=receiver,
        expected_pubsub_email="quietpilot-push@example.iam.gserviceaccount.com",
    )

    assert response["statusCode"] == 401
    assert receiver.calls == []


def test_pubsub_rejects_malformed_history_without_logging_private_data(caplog) -> None:
    receiver = _HistoryReceiver()
    with caplog.at_level(logging.INFO, logger="quietpilot_ingress.handlers"):
        response = handle_request(
            _pubsub_event(
                data={"emailAddress": "private@example.com", "historyId": "bad"}
            ),
            lambda state, session: "unused",
            google_history_receiver=receiver,
            expected_pubsub_email="quietpilot-push@example.iam.gserviceaccount.com",
        )

    assert response["statusCode"] == 400
    assert receiver.calls == []
    assert "HISTORY_FORMAT_INVALID" in caplog.text
    assert "private@example.com" not in caplog.text
    assert "historyId" not in caplog.text


def test_pubsub_acknowledges_stale_unknown_account_without_queueing() -> None:
    receiver = _HistoryReceiver(result=False)

    response = handle_request(
        _pubsub_event(),
        lambda state, session: "unused",
        google_history_receiver=receiver,
        expected_pubsub_email="quietpilot-push@example.iam.gserviceaccount.com",
    )

    assert response["statusCode"] == 204
