import copy
import json

import pytest
from quietpilot_worker.consumer import handle_sqs_batch
from quietpilot_worker.idempotency import (
    DynamoIdempotencyStore,
    IdempotencyConflict,
    IdempotencyInProgress,
)


class ConditionalCheckFailedException(Exception):
    pass


class _Exceptions:
    ConditionalCheckFailedException = ConditionalCheckFailedException


class FakeDynamoDb:
    exceptions = _Exceptions()

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, dict[str, str]]] = {}

    def put_item(self, *, Item, **kwargs) -> None:
        del kwargs
        key = self._key(Item)
        if key in self.items:
            raise ConditionalCheckFailedException
        self.items[key] = copy.deepcopy(Item)

    def get_item(self, *, Key, **kwargs):
        del kwargs
        item = self.items.get(self._key(Key))
        return {"Item": copy.deepcopy(item)} if item else {}

    def update_item(
        self,
        *,
        Key,
        ExpressionAttributeValues,
        **kwargs,
    ) -> None:
        del kwargs
        item = self.items[self._key(Key)]
        values = ExpressionAttributeValues
        status = item["status"]["S"]
        current_token = item.get("lockToken", {}).get("S")

        if ":completed" in values:
            if status != "IN_PROGRESS" or current_token != values[":token"]["S"]:
                raise ConditionalCheckFailedException
            item["status"] = copy.deepcopy(values[":completed"])
            item["expiresAt"] = copy.deepcopy(values[":expiry"])
            item["updatedAt"] = copy.deepcopy(values[":updated"])
            item.pop("lockToken", None)
            item.pop("inProgressExpiresAt", None)
            return

        if ":failed" in values and ":payload_hash" not in values:
            if status != "IN_PROGRESS" or current_token != values[":token"]["S"]:
                raise ConditionalCheckFailedException
            item["status"] = copy.deepcopy(values[":failed"])
            item["inProgressExpiresAt"] = copy.deepcopy(values[":now"])
            item["expiresAt"] = copy.deepcopy(values[":expiry"])
            item["updatedAt"] = copy.deepcopy(values[":updated"])
            item.pop("lockToken", None)
            return

        now = int(values[":now"]["N"])
        expires_at = int(item["expiresAt"]["N"])
        in_progress_expires_at = int(item.get("inProgressExpiresAt", {"N": "0"})["N"])
        reclaimable = (
            expires_at <= now
            or status == "FAILED"
            or (status == "IN_PROGRESS" and in_progress_expires_at <= now)
        )
        if item["payloadHash"]["S"] != values[":payload_hash"]["S"] or not reclaimable:
            raise ConditionalCheckFailedException
        item["status"] = copy.deepcopy(values[":in_progress"])
        item["lockToken"] = copy.deepcopy(values[":token"])
        item["inProgressExpiresAt"] = copy.deepcopy(values[":in_progress_expiry"])
        item["expiresAt"] = copy.deepcopy(values[":expiry"])
        item["updatedAt"] = copy.deepcopy(values[":updated"])

    @staticmethod
    def _key(values) -> tuple[str, str]:
        return (
            values["subject"]["S"],
            values["idempotencyKey"]["S"],
        )


def _envelope(*, payload: str = "original") -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": "event-1",
        "event_type": "DIRECT_REQUEST_RECEIVED",
        "user_id": "private-user-id",
        "connector": "direct",
        "occurred_at": "2026-08-28T00:00:00Z",
        "dedupe_key": "private-dedupe-key",
        "trace_id": "trace-1",
        "payload": {"value": payload},
    }


def _store(client: FakeDynamoDb, now: list[float]) -> DynamoIdempotencyStore:
    tokens = iter(["token-1", "token-2", "token-3", "token-4"])
    return DynamoIdempotencyStore(
        "idempotency-table",
        client,
        in_progress_ttl_seconds=30,
        completed_ttl_seconds=300,
        clock=lambda: now[0],
        token_factory=lambda: next(tokens),
    )


def test_completed_duplicate_skips_processor_and_stores_no_raw_identity() -> None:
    client = FakeDynamoDb()
    now = [1000.0]
    store = _store(client, now)
    calls: list[str] = []

    assert store.run(_envelope(), lambda event: calls.append(str(event["event_id"])))
    assert not store.run(
        _envelope(), lambda event: calls.append(str(event["event_id"]))
    )

    assert calls == ["event-1"]
    stored = json.dumps(
        [{"key": list(key), "item": item} for key, item in client.items.items()],
        sort_keys=True,
    )
    assert "private-user-id" not in stored
    assert "private-dedupe-key" not in stored
    assert "original" not in stored
    assert next(iter(client.items.values()))["status"] == {"S": "COMPLETED"}


def test_same_dedupe_key_rejects_changed_payload() -> None:
    client = FakeDynamoDb()
    store = _store(client, [1000.0])
    store.run(_envelope(), lambda event: None)

    with pytest.raises(IdempotencyConflict, match="payload"):
        store.run(_envelope(payload="changed"), lambda event: None)


def test_transport_metadata_change_does_not_repeat_same_operation() -> None:
    client = FakeDynamoDb()
    store = _store(client, [1000.0])
    calls: list[str] = []
    store.run(_envelope(), lambda event: calls.append("processed"))
    redelivery = _envelope()
    redelivery["event_id"] = "event-2"
    redelivery["trace_id"] = "trace-2"
    redelivery["occurred_at"] = "2026-08-28T00:01:00Z"

    assert not store.run(redelivery, lambda event: calls.append("repeated"))
    assert calls == ["processed"]


def test_active_claim_rejects_concurrent_processor() -> None:
    client = FakeDynamoDb()
    store = _store(client, [1000.0])

    with pytest.raises(SystemExit):
        store.run(_envelope(), lambda event: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(IdempotencyInProgress, match="still in progress"):
        store.run(_envelope(), lambda event: None)


def test_expired_in_progress_claim_can_be_recovered() -> None:
    client = FakeDynamoDb()
    now = [1000.0]
    store = _store(client, now)
    with pytest.raises(SystemExit):
        store.run(_envelope(), lambda event: (_ for _ in ()).throw(SystemExit()))

    now[0] = 1031.0
    calls: list[str] = []
    assert store.run(_envelope(), lambda event: calls.append("recovered"))
    assert calls == ["recovered"]


def test_failed_processor_releases_record_for_retry() -> None:
    client = FakeDynamoDb()
    store = _store(client, [1000.0])
    calls = 0

    def processor(event) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("controlled failure")

    with pytest.raises(RuntimeError, match="controlled"):
        store.run(_envelope(), processor)
    assert store.run(_envelope(), processor)
    assert calls == 2


def test_consumer_acknowledges_completed_duplicate_without_rerunning() -> None:
    client = FakeDynamoDb()
    store = _store(client, [1000.0])
    calls: list[str] = []
    record = {"messageId": "message-1", "body": json.dumps(_envelope())}

    first = handle_sqs_batch(
        {"Records": [record]},
        lambda event: calls.append(str(event["event_id"])),
        idempotency=store,
    )
    second = handle_sqs_batch(
        {"Records": [record]},
        lambda event: calls.append(str(event["event_id"])),
        idempotency=store,
    )

    assert first == {"batchItemFailures": []}
    assert second == {"batchItemFailures": []}
    assert calls == ["event-1"]
