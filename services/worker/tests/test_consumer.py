import json
import logging

import pytest
from quietpilot_worker import consumer
from quietpilot_worker.consumer import handle_sqs_batch


def _envelope(event_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": "DIRECT_REQUEST_RECEIVED",
        "user_id": "tenant|user",
        "connector": "direct",
        "occurred_at": "2026-08-24T00:00:00Z",
        "dedupe_key": f"direct:{event_id}",
        "trace_id": "trace-1",
        "payload": {"private": "must-not-be-logged"},
    }


def _record(message_id: str, body: object) -> dict[str, object]:
    return {
        "messageId": message_id,
        "body": body if isinstance(body, str) else json.dumps(body),
    }


def test_partial_batch_returns_only_failed_message_ids() -> None:
    def processor(envelope: dict[str, object]) -> None:
        if envelope["event_id"] == "bad":
            raise RuntimeError("controlled failure")

    result = handle_sqs_batch(
        {
            "Records": [
                _record("message-good", _envelope("good")),
                _record("message-bad", _envelope("bad")),
            ]
        },
        processor,
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "message-bad"}]}


def test_invalid_envelope_fails_without_calling_processor() -> None:
    called = False

    def processor(envelope: dict[str, object]) -> None:
        nonlocal called
        called = True

    result = handle_sqs_batch(
        {"Records": [_record("invalid", {"schema_version": 1})]},
        processor,
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "invalid"}]}
    assert called is False


def test_boolean_schema_version_is_not_integer_one() -> None:
    envelope = _envelope("bool-version")
    envelope["schema_version"] = True
    result = handle_sqs_batch(
        {"Records": [_record("bool-version", envelope)]},
        lambda value: None,
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "bool-version"}]}


@pytest.mark.parametrize(
    "occurred_at",
    [
        "not-a-date",
        "2026-08-24T00:00:00",
        "20260824T000000+00:00",
        "2026-W34-7T00:00:00+00:00",
        "2026-08-24 00:00:00+00:00",
        "2026-08-24T00:00:00+00:00:30",
        "2026-08-24T00:00:00,5Z",
    ],
)
def test_occurred_at_requires_rfc3339_timezone(occurred_at: str) -> None:
    envelope = _envelope("bad-time")
    envelope["occurred_at"] = occurred_at
    result = handle_sqs_batch(
        {"Records": [_record("bad-time", envelope)]},
        lambda value: None,
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "bad-time"}]}


def test_foundation_handler_fails_closed_until_processor_is_wired(monkeypatch) -> None:
    class PassThroughIdempotency:
        def run(self, envelope, processor) -> bool:
            processor(envelope)
            return True

    class Context:
        aws_request_id = "request-1"

    monkeypatch.setattr(
        consumer,
        "default_idempotency_store",
        lambda context: PassThroughIdempotency(),
    )
    result = consumer.handler(
        {"Records": [_record("deferred", _envelope("event-1"))]},
        Context(),
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "deferred"}]}


def test_exact_schedule_target_runs_bounded_maintenance(monkeypatch) -> None:
    class Context:
        aws_request_id = "request-1"

    class Maintenance:
        def run(self, task: str) -> dict[str, object]:
            return {"status": "COMPLETED", "task": task}

    monkeypatch.setattr(
        consumer,
        "default_google_maintenance",
        lambda: Maintenance(),
    )
    assert consumer.handler({"maintenance_task": "RENEW_GMAIL_WATCHES"}, Context()) == {
        "status": "COMPLETED",
        "task": "RENEW_GMAIL_WATCHES",
    }


def test_unknown_or_expanded_schedule_target_fails_closed() -> None:
    class Context:
        aws_request_id = "request-1"

    for event in (
        {"maintenance_task": "UNKNOWN"},
        {"maintenance_task": "RENEW_GMAIL_WATCHES", "extra": True},
    ):
        with pytest.raises(ValueError, match="invalid"):
            consumer.handler(event, Context())


@pytest.mark.parametrize(
    "records",
    ["not-a-list", None, {}, [], [{"body": "{}"}], [{"messageId": "", "body": "{}"}]],
)
def test_malformed_sqs_batch_fails_the_whole_invocation(records: object) -> None:
    with pytest.raises((RuntimeError, TypeError, ValueError)):
        handle_sqs_batch({"Records": records}, lambda envelope: None)


def test_logs_exclude_message_body_and_user_id(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="quietpilot_worker.consumer"):
        handle_sqs_batch(
            {"Records": [_record("message-1", _envelope("event-1"))]},
            lambda envelope: None,
            request_id="request-1",
        )
    text = caplog.text
    assert "request-1" in text
    assert "must-not-be-logged" not in text
    assert "tenant|user" not in text


def test_failed_record_logs_only_safe_stage_and_error_class(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="quietpilot_worker.consumer"):
        result = handle_sqs_batch(
            {"Records": [_record("message-1", _envelope("event-1"))]},
            lambda envelope: (_ for _ in ()).throw(RuntimeError("private detail")),
            request_id="request-1",
        )

    assert result == {"batchItemFailures": [{"itemIdentifier": "message-1"}]}
    text = caplog.text
    assert '"event":"record_failed"' in text
    assert '"event_type":"DIRECT_REQUEST_RECEIVED"' in text
    assert '"stage":"idempotency_or_processor"' in text
    assert '"error_class":"RuntimeError"' in text
    assert "private detail" not in text
    assert "must-not-be-logged" not in text
    assert "tenant|user" not in text


def test_low_remaining_time_defers_model_record_but_can_process_short_push_record():
    called = []
    remaining = iter([120_000, 90_000, 30_000])
    push = {
        **_envelope("push"),
        "event_type": "NOTIFICATION_DISPATCH_REQUESTED",
        "connector": "system",
        "payload": {"notification_id": "a" * 64},
    }
    result = handle_sqs_batch(
        {
            "Records": [
                _record("first", _envelope("first")),
                _record("later", _envelope("later")),
                _record("push", push),
            ]
        },
        lambda e: called.append(e["event_id"]),
        remaining_time_ms=lambda: next(remaining),
    )
    assert called == ["first", "push"]
    assert result == {"batchItemFailures": [{"itemIdentifier": "later"}]}


def test_case_wakeup_uses_exact_persisted_event_and_is_repeatable(monkeypatch):
    from types import SimpleNamespace

    from quietpilot_worker.notifications import notification_put

    meta = {
        "user_id": {"S": "owner-a"},
        "version": {"N": "3"},
        "status": {"S": "DECISION_REQUIRED"},
        "updated_at": {"S": "2026-09-15T00:00:00Z"},
    }
    notification = notification_put(
        "table", "owner-a", "case-a", 3, "DECISION_REQUIRED", "2026-09-15T00:00:00Z"
    )["Put"]["Item"]
    reads, sent = [], []

    def get_item(**kw):
        assert kw["ConsistentRead"] is True
        reads.append(kw["Key"])
        if kw["Key"]["SK"]["S"] == "META":
            return {"Item": meta}
        return {"Item": notification} if kw["Key"]["SK"] == notification["SK"] else {}

    monkeypatch.setenv("MAIN_TABLE_NAME", "table")
    monkeypatch.setenv("WORK_QUEUE_URL", "queue")
    monkeypatch.setattr(
        "boto3.client",
        lambda name: (
            SimpleNamespace(get_item=get_item)
            if name == "dynamodb"
            else SimpleNamespace(send_message=lambda **kw: sent.append(kw))
        ),
    )
    envelope = {"user_id": "owner-a", "payload": {"case_id": "case-a"}}
    consumer._queue_case_notifications(envelope)
    consumer._queue_case_notifications(envelope)
    assert len(sent) == 2 and sent[0] == sent[1]
    message = json.loads(sent[0]["MessageBody"])
    assert message["event_type"] == "NOTIFICATION_DISPATCH_REQUESTED"
    assert message["payload"] == {"notification_id": notification["event_id"]["S"]}
    assert "expo_push_token" not in sent[0]["MessageBody"]
    meta["status"] = {"S": "COMPLETED"}
    consumer._queue_case_notifications(envelope)
    assert len(sent) == 2
    meta["user_id"] = {"S": "other"}
    consumer._queue_case_notifications(envelope)
    assert len(sent) == 2


def test_exact_notification_dispatch_does_not_call_model_processors(monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setenv("MAIN_TABLE_NAME", "table")
    monkeypatch.setattr("boto3.client", lambda name: object())
    monkeypatch.setattr(
        "quietpilot_worker.notifications.NotificationDispatcher",
        lambda *args: SimpleNamespace(
            flush=lambda owner, **kw: calls.append((owner, kw)) or {"busy": 0}
        ),
    )
    monkeypatch.setattr(
        consumer,
        "default_case_job_processor",
        lambda: pytest.fail("Push must not invoke a model"),
    )
    envelope = {
        **_envelope("push"),
        "user_id": "owner-a",
        "event_type": "NOTIFICATION_DISPATCH_REQUESTED",
        "connector": "system",
        "payload": {"notification_id": "a" * 64},
    }
    consumer._foundation_processor(envelope)
    assert calls == [("owner-a", {"event_id": "a" * 64})]
    envelope["payload"]["url"] = "https://untrusted.test"
    with pytest.raises(ValueError):
        consumer._foundation_processor(envelope)
