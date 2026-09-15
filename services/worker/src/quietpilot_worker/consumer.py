"""SQS partial-batch Lambda entrypoint with a fail-closed processor boundary."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from .calendar_execution import default_calendar_execution_processor
from .case_jobs import default_case_job_processor
from .google_jobs import default_google_job_processor
from .google_maintenance import (
    ALLOWED_MAINTENANCE_TASKS,
    default_google_maintenance,
)
from .idempotency import DynamoIdempotencyStore, IdempotencyGate

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ALLOWED_EVENT_TYPES = frozenset(
    {
        "INITIAL_SCAN_REQUESTED",
        "INITIAL_SCAN_CONTINUATION",
        "MAIL_SETUP_REQUESTED",
        "MAIL_RECOMMENDATIONS_REQUESTED",
        "MAIL_SCAN_REQUESTED",
        "MAIL_SCAN_CONTINUATION",
        "GMAIL_HISTORY_AVAILABLE",
        "DIRECT_REQUEST_RECEIVED",
        "CAPABILITY_SCAN_REQUESTED",
        "PLAN_APPROVED",
        "ACTION_EXECUTE_REQUESTED",
        "ACTION_VERIFY_REQUESTED",
        "NOTIFICATION_DISPATCH_REQUESTED",
        "GOOGLE_CONNECTION_REVOKED",
        "SMARTTHINGS_DEVICE_EVENT",
        "SMARTTHINGS_CONNECTION_DELETED",
    }
)

EventProcessor = Callable[[dict[str, object]], None]
RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


def handler(event: Mapping[str, Any], context: object) -> dict[str, object]:
    """Fail every unconfigured work item individually; never acknowledge lost work."""

    maintenance_task = event.get("maintenance_task")
    if maintenance_task is not None:
        if set(event) != {"maintenance_task"} or maintenance_task not in (
            ALLOWED_MAINTENANCE_TASKS
        ):
            raise ValueError("scheduled maintenance event is invalid")
        return default_google_maintenance().run(str(maintenance_task))

    records = _validated_records(event)
    request_id = str(getattr(context, "aws_request_id", "unknown"))
    return _handle_sqs_records(
        records,
        _foundation_processor,
        idempotency=default_idempotency_store(context),
        request_id=request_id,
        remaining_time_ms=getattr(context, "get_remaining_time_in_millis", None),
    )


def handle_sqs_batch(
    event: Mapping[str, Any],
    processor: EventProcessor,
    *,
    idempotency: IdempotencyGate | None = None,
    request_id: str = "test",
    remaining_time_ms: Callable[[], int] | None = None,
) -> dict[str, object]:
    return _handle_sqs_records(
        _validated_records(event),
        processor,
        idempotency=idempotency,
        request_id=request_id,
        remaining_time_ms=remaining_time_ms,
    )


def _handle_sqs_records(
    records: list[Mapping[str, Any]],
    processor: EventProcessor,
    *,
    idempotency: IdempotencyGate | None,
    request_id: str,
    remaining_time_ms: Callable[[], int] | None = None,
) -> dict[str, object]:

    failures: list[dict[str, str]] = []
    for record in records:
        message_id = str(record["messageId"])
        stage = "parse_envelope"
        event_type = "UNKNOWN"
        try:
            envelope = _parse_envelope(record.get("body"))
            event_type = str(envelope["event_type"])
            # A model record may consume the configured 90-second read timeout.
            # Leave space for persistence and a partial-batch response; a push
            # record has no model call and sends to at most five devices.
            required_ms = (
                25_000 if event_type == "NOTIFICATION_DISPATCH_REQUESTED" else 100_000
            )
            if callable(remaining_time_ms) and remaining_time_ms() < required_ms:
                failures.append({"itemIdentifier": message_id})
                continue
            stage = "idempotency_or_processor"
            if idempotency is None:
                processor(envelope)
            else:
                idempotency.run(envelope, processor)
        except Exception as error:  # noqa: BLE001 - isolate one failed record
            logger.warning(
                json.dumps(
                    {
                        "event": "record_failed",
                        "request_id": request_id,
                        "event_type": event_type,
                        "stage": stage,
                        "error_class": type(error).__name__,
                    },
                    separators=(",", ":"),
                )
            )
            failures.append({"itemIdentifier": message_id})

    logger.info(
        json.dumps(
            {
                "event": "batch_completed",
                "request_id": request_id,
                "record_count": len(records),
                "failure_count": len(failures),
            },
            separators=(",", ":"),
        )
    )
    return {"batchItemFailures": failures}


def default_idempotency_store(context: object) -> DynamoIdempotencyStore:
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    remaining_milliseconds = remaining() if callable(remaining) else 120_000
    if type(remaining_milliseconds) is not int or remaining_milliseconds < 1:
        remaining_milliseconds = 120_000
    in_progress_seconds = math.ceil(remaining_milliseconds / 1000) + 30
    return DynamoIdempotencyStore.from_environment(
        in_progress_ttl_seconds=in_progress_seconds,
    )


def _validated_records(event: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records = event.get("Records")
    if not isinstance(records, list) or not records:
        raise RuntimeError(
            "SQS batch is malformed or scheduled maintenance is disabled"
        )
    validated: list[Mapping[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("SQS record must be an object")
        message_id = record.get("messageId")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("SQS record must contain a non-empty messageId")
        validated.append(record)
    return validated


def _parse_envelope(body: object) -> dict[str, object]:
    if not isinstance(body, str):
        raise TypeError("message body must be JSON text")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise TypeError("message body must be an object")
    required = {
        "schema_version",
        "event_id",
        "event_type",
        "user_id",
        "connector",
        "occurred_at",
        "dedupe_key",
        "trace_id",
        "payload",
    }
    if set(value) != required:
        raise ValueError("event envelope fields do not match schema version 1")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("unsupported schema version")
    if value["event_type"] not in ALLOWED_EVENT_TYPES:
        raise ValueError("unsupported event type")
    if not isinstance(value["payload"], dict):
        raise TypeError("payload must be an object")
    for field in (
        "event_id",
        "user_id",
        "connector",
        "occurred_at",
        "dedupe_key",
        "trace_id",
    ):
        if not isinstance(value[field], str) or not value[field]:
            raise ValueError(f"{field} must be non-empty text")
    if value["connector"] not in {"google", "smartthings", "direct", "system"}:
        raise ValueError("unsupported connector")
    occurred_at = str(value["occurred_at"])
    if RFC3339_PATTERN.fullmatch(occurred_at) is None:
        raise ValueError("occurred_at must be RFC 3339 date-time")
    normalized_time = (
        f"{occurred_at[:-1]}+00:00" if occurred_at.endswith(("Z", "z")) else occurred_at
    )
    try:
        parsed_time = datetime.fromisoformat(normalized_time)
    except ValueError as error:
        raise ValueError("occurred_at must be RFC 3339 date-time") from error
    if parsed_time.tzinfo is None:
        raise ValueError("occurred_at must include a timezone")
    return value


def _foundation_processor(envelope: dict[str, object]) -> None:
    if envelope.get("event_type") == "NOTIFICATION_DISPATCH_REQUESTED":
        import boto3

        from .notifications import NotificationDispatcher

        payload = envelope.get("payload")
        if (
            envelope.get("connector") != "system"
            or not isinstance(payload, dict)
            or set(payload) != {"notification_id"}
            or not isinstance(payload["notification_id"], str)
            or re.fullmatch(r"[0-9a-f]{64}", payload["notification_id"]) is None
        ):
            raise ValueError("Notification dispatch envelope is invalid")
        counts = NotificationDispatcher(
            os.environ["MAIN_TABLE_NAME"], boto3.client("dynamodb")
        ).flush(str(envelope["user_id"]), event_id=payload["notification_id"])
        if counts["busy"]:
            raise RuntimeError("Notification is already being processed")
        return
    if envelope.get("event_type") == "ACTION_EXECUTE_REQUESTED":
        default_calendar_execution_processor().process(envelope)
        _queue_case_notifications(envelope)
        return
    if envelope.get("event_type") == "DIRECT_REQUEST_RECEIVED":
        default_case_job_processor().process(envelope)
        _queue_case_notifications(envelope)
        return
    default_google_job_processor().process(envelope)
    if envelope.get("event_type") in {
        "MAIL_SCAN_REQUESTED",
        "MAIL_SCAN_CONTINUATION",
        "GMAIL_HISTORY_AVAILABLE",
        "INITIAL_SCAN_REQUESTED",
        "INITIAL_SCAN_CONTINUATION",
    }:
        from .routines import default_routine_processor

        result = default_routine_processor().process(str(envelope["user_id"]))
        if result.get("limit_reached"):
            raise RuntimeError("Routine preparation has bounded work remaining")


def _queue_case_notifications(envelope: Mapping[str, object]) -> None:
    """Wake the exact persisted outbox event after a Case transaction.

    Strong reads avoid relying on immediate GSI visibility. A source-job retry
    can safely re-send the same dispatch envelope after an uncertain SQS send.
    """
    import boto3

    from .notifications import notification_put

    user = str(envelope["user_id"])
    payload = envelope.get("payload")
    case_id = payload.get("case_id") if isinstance(payload, Mapping) else None
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("Case notification source is invalid")
    table = os.environ["MAIN_TABLE_NAME"]
    client = boto3.client("dynamodb")
    meta = client.get_item(
        TableName=table,
        Key={"PK": {"S": f"CASE#{case_id}"}, "SK": {"S": "META"}},
        ConsistentRead=True,
    ).get("Item", {})
    if meta.get("user_id", {}).get("S") != user:
        return
    if meta.get("status", {}).get("S") not in {
        "DECISION_REQUIRED",
        "FAILED",
        "PERMISSION_REVOKED",
    }:
        return
    version = int(meta["version"]["N"])
    for kind in ("DECISION_REQUIRED", "ACTION_FAILED", "PLAN_CHANGED"):
        proposed = notification_put(
            table, user, case_id, version, kind, meta["updated_at"]["S"]
        )["Put"]["Item"]
        event = client.get_item(
            TableName=table,
            Key={"PK": proposed["PK"], "SK": proposed["SK"]},
            ConsistentRead=True,
        ).get("Item", {})
        if event.get("status", {}).get("S") not in {"PENDING", "SENDING"}:
            continue
        identifier = event["event_id"]["S"]
        message = {
            "schema_version": 1,
            "event_id": f"push-{identifier}",
            "event_type": "NOTIFICATION_DISPATCH_REQUESTED",
            "user_id": user,
            "connector": "system",
            "occurred_at": event["created_at"]["S"],
            "dedupe_key": f"push:{identifier}",
            "trace_id": identifier,
            "payload": {"notification_id": identifier},
        }
        boto3.client("sqs").send_message(
            QueueUrl=os.environ["WORK_QUEUE_URL"],
            MessageBody=json.dumps(message, separators=(",", ":")),
        )
