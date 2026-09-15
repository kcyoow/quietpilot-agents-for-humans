"""Public, token-free ingress routes for provider redirects and webhooks."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import secrets
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
SESSION_URI_PATTERN = re.compile(
    r"^urn:ietf:params:oauth:request_uri:[A-Za-z0-9._~-]+$"
)


class ReturnCodeCreator(Protocol):
    def __call__(self, state: str, session_id: str) -> str: ...


class GoogleHistoryReceiver(Protocol):
    def receive(
        self,
        *,
        account_hash: str,
        history_id: str,
        message_id: str,
    ) -> bool: ...


class PubSubInputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def handler(event: Mapping[str, Any], context: object) -> dict[str, object]:
    del context
    return handle_request(event, _create_oauth_return_code)


def handle_request(
    event: Mapping[str, Any],
    create_return_code: ReturnCodeCreator,
    *,
    google_history_receiver: GoogleHistoryReceiver | None = None,
    expected_pubsub_email: str | None = None,
) -> dict[str, object]:
    request_context = _mapping(event.get("requestContext"))
    request_id = str(request_context.get("requestId", "unknown"))
    http = _mapping(request_context.get("http"))
    method = str(http.get("method", ""))
    path = str(http.get("path", ""))
    if method == "POST" and path == "/webhooks/google/pubsub":
        return _handle_google_pubsub(
            event,
            request_context=request_context,
            request_id=request_id,
            receiver=google_history_receiver
            or DynamoGoogleHistoryReceiver.from_environment(),
            expected_email=expected_pubsub_email
            or _required_environment("GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL"),
        )
    if method != "GET" or path != "/oauth/google/callback":
        return _json_response(404, {"error": "NOT_FOUND"})

    query = _mapping(event.get("queryStringParameters"))
    state = query.get("state")
    session_id = query.get("session_id")
    if (
        not isinstance(state, str)
        or STATE_PATTERN.fullmatch(state) is None
        or not isinstance(session_id, str)
        or SESSION_URI_PATTERN.fullmatch(session_id) is None
    ):
        _log_completion(request_id, 400)
        return _html_response(
            400,
            "Google 연결 정보를 확인하지 못했어요. 앱에서 다시 연결해 주세요.",
        )

    try:
        code = create_return_code(state, session_id)
    except Exception:
        logger.exception(
            json.dumps(
                {
                    "event": "oauth_callback_failed",
                    "request_id": request_id,
                },
                separators=(",", ":"),
            )
        )
        return _html_response(
            503,
            "Google 연결 확인이 지연되고 있어요. 앱에서 다시 시도해 주세요.",
        )
    destination = (
        f"quietpilot://oauth-return?provider=google&code={quote(code, safe='')}"
    )
    _log_completion(request_id, 302)
    return {
        "statusCode": 302,
        "headers": {
            "location": destination,
            "cache-control": "no-store",
            "referrer-policy": "no-referrer",
        },
        "body": "",
    }


def _handle_google_pubsub(
    event: Mapping[str, Any],
    *,
    request_context: Mapping[str, Any],
    request_id: str,
    receiver: GoogleHistoryReceiver,
    expected_email: str,
) -> dict[str, object]:
    claims = _mapping(
        _mapping(_mapping(request_context.get("authorizer")).get("jwt")).get("claims")
    )
    claim_email = claims.get("email")
    email_verified = claims.get("email_verified")
    if (
        not isinstance(claim_email, str)
        or claim_email.casefold() != expected_email.casefold()
        or email_verified not in (True, "true", "True")
    ):
        _log_pubsub_completion(request_id, 401, queued=False)
        return _json_response(401, {"error": "INVALID_GOOGLE_IDENTITY"})

    try:
        message = _google_pubsub_message(event)
        account_hash = _google_account_hash(message["email_address"])
        queued = receiver.receive(
            account_hash=account_hash,
            history_id=message["history_id"],
            message_id=message["message_id"],
        )
    except PubSubInputError as error:
        _log_pubsub_completion(
            request_id,
            400,
            queued=False,
            error_code=error.code,
        )
        return _json_response(400, {"error": "INVALID_GOOGLE_PUBSUB_BODY"})
    except Exception:
        logger.exception(
            json.dumps(
                {"event": "google_pubsub_failed", "request_id": request_id},
                separators=(",", ":"),
            )
        )
        return _json_response(503, {"error": "GOOGLE_PUBSUB_UNAVAILABLE"})

    _log_pubsub_completion(request_id, 204, queued=queued)
    return {
        "statusCode": 204,
        "headers": {"cache-control": "no-store"},
        "body": "",
    }


def _google_pubsub_message(event: Mapping[str, Any]) -> dict[str, str]:
    if event.get("isBase64Encoded") is True:
        raise PubSubInputError("OUTER_BODY_ENCODING")
    body = event.get("body")
    if not isinstance(body, str) or len(body.encode("utf-8")) > 64 * 1024:
        raise PubSubInputError("OUTER_BODY_INVALID")
    try:
        document = json.loads(body)
    except json.JSONDecodeError as error:
        raise PubSubInputError("OUTER_BODY_JSON") from error
    message = _mapping(_mapping(document).get("message"))
    try:
        message_id = _bounded_text(message.get("messageId"), maximum=256)
    except ValueError as error:
        raise PubSubInputError("MESSAGE_ID_INVALID") from error
    try:
        encoded = _bounded_text(message.get("data"), maximum=32 * 1024)
    except ValueError as error:
        raise PubSubInputError("DATA_FIELD_INVALID") from error
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except binascii.Error as error:
        raise PubSubInputError("DATA_ENCODING_INVALID") from error
    try:
        data = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PubSubInputError("DATA_JSON_INVALID") from error
    values = _mapping(data)
    try:
        email_address = _bounded_text(values.get("emailAddress"), maximum=320)
    except ValueError as error:
        raise PubSubInputError("EMAIL_INVALID") from error
    raw_history_id = values.get("historyId")
    if type(raw_history_id) is int:
        history_id = str(raw_history_id)
    elif isinstance(raw_history_id, str):
        history_id = raw_history_id
    else:
        raise PubSubInputError("HISTORY_TYPE_INVALID")
    try:
        history_id = _bounded_text(history_id, maximum=64)
    except ValueError as error:
        raise PubSubInputError("HISTORY_FORMAT_INVALID") from error
    if not history_id.isdigit():
        raise PubSubInputError("HISTORY_FORMAT_INVALID")
    return {
        "message_id": message_id,
        "email_address": email_address,
        "history_id": history_id,
    }


class DynamoGoogleHistoryReceiver:
    def __init__(
        self,
        *,
        table_name: str,
        queue_url: str,
        dynamodb: Any,
        sqs: Any,
    ) -> None:
        self._table_name = table_name
        self._queue_url = queue_url
        self._dynamodb = dynamodb
        self._sqs = sqs

    @classmethod
    def from_environment(cls) -> DynamoGoogleHistoryReceiver:
        import boto3

        return cls(
            table_name=_required_environment("MAIN_TABLE_NAME"),
            queue_url=_required_environment("WORK_QUEUE_URL"),
            dynamodb=boto3.client("dynamodb"),
            sqs=boto3.client("sqs"),
        )

    def receive(
        self,
        *,
        account_hash: str,
        history_id: str,
        message_id: str,
    ) -> bool:
        response = self._dynamodb.query(
            TableName=self._table_name,
            IndexName="GSI1",
            KeyConditionExpression="#pk=:pk AND #sk=:sk",
            ExpressionAttributeNames={"#pk": "GSI1PK", "#sk": "GSI1SK"},
            ExpressionAttributeValues={
                ":pk": {"S": f"GOOGLE_ACCOUNT#{account_hash}"},
                ":sk": {"S": "CONNECTION#google"},
            },
            ConsistentRead=False,
            Limit=2,
        )
        items = response.get("Items")
        if not isinstance(items, list) or not items:
            return False
        if len(items) != 1:
            raise RuntimeError("Google account maps to multiple owners")
        user_id = _dynamo_string(_mapping(items[0]), "user_id")
        if user_id is None or len(user_id) > 256:
            raise RuntimeError("Google connection owner is unavailable")

        source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"google-pubsub:{message_id}"))
        envelope = {
            "schema_version": 1,
            "event_id": source_id,
            "event_type": "GMAIL_HISTORY_AVAILABLE",
            "user_id": user_id,
            "connector": "google",
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "dedupe_key": f"google:gmail-history:{account_hash}:{history_id}",
            "trace_id": str(uuid.uuid4()),
            "payload": {"history_id": history_id},
        }
        self._sqs.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(envelope, separators=(",", ":")),
        )
        return True


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_response(status_code: int, body: dict[str, object]) -> dict[str, object]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, separators=(",", ":")),
    }


def _html_response(status_code: int, message: str) -> dict[str, object]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "text/html; charset=utf-8",
            "cache-control": "no-store",
            "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'",
            "referrer-policy": "no-referrer",
        },
        "body": (
            "<!doctype html><html lang=ko><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>QuietPilot</title><body>"
            f"<p>{message}</p></body></html>"
        ),
    }


def _log_completion(request_id: str, status: int) -> None:
    logger.info(
        json.dumps(
            {
                "event": "oauth_callback_completed",
                "request_id": request_id,
                "status": status,
            },
            separators=(",", ":"),
        )
    )


def _log_pubsub_completion(
    request_id: str,
    status: int,
    *,
    queued: bool,
    error_code: str | None = None,
) -> None:
    record: dict[str, object] = {
        "event": "google_pubsub_completed",
        "request_id": request_id,
        "status": status,
        "queued": queued,
    }
    if error_code is not None:
        record["error_code"] = error_code
    logger.info(json.dumps(record, separators=(",", ":")))


def _google_account_hash(email_address: str) -> str:
    return hashlib.sha256(email_address.strip().casefold().encode("utf-8")).hexdigest()


def _bounded_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError("text value is invalid")
    return value


def _create_oauth_return_code(state: str, session_id: str) -> str:
    import boto3

    table_name = os.environ.get("MAIN_TABLE_NAME", "").strip()
    if not table_name:
        raise RuntimeError("MAIN_TABLE_NAME is not configured")
    client = boto3.client("dynamodb")
    state_digest = hashlib.sha256(state.encode("ascii")).hexdigest()
    response = client.get_item(
        TableName=table_name,
        Key={
            "PK": {"S": f"OAUTH#{state_digest}"},
            "SK": {"S": "FLOW#google"},
        },
        ConsistentRead=True,
    )
    item = response.get("Item")
    if not isinstance(item, Mapping):
        raise TypeError("OAuth flow was not found")
    stored_session = _dynamo_string(item, "session_uri")
    user_id = _dynamo_string(item, "user_id")
    expires_at = _dynamo_string(item, "expiresAt", attribute_type="N")
    status = _dynamo_string(item, "status")
    if (
        stored_session != session_id
        or user_id is None
        or expires_at is None
        or int(expires_at) < int(datetime.now(UTC).timestamp())
        or status != "PENDING"
    ):
        raise RuntimeError("OAuth flow did not match")

    code = secrets.token_urlsafe(32)
    code_digest = hashlib.sha256(code.encode("ascii")).hexdigest()
    client.put_item(
        TableName=table_name,
        Item={
            "PK": {"S": f"RETURN#{code_digest}"},
            "SK": {"S": "OAUTH#google"},
            "entity_type": {"S": "oauth_return"},
            "user_id": {"S": user_id},
            "state": {"S": state},
            "session_uri": {"S": session_id},
            "status": {"S": "PENDING"},
            "expiresAt": {
                "N": str(int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()))
            },
        },
        ConditionExpression="attribute_not_exists(PK)",
    )
    return code


def _dynamo_string(
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
