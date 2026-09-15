from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from quietpilot_worker import consumer
from quietpilot_worker.calendar_execution import (
    CALENDAR_SCOPE,
    GMAIL_SCOPE,
    CalendarExecutionLeaseLost,
    CalendarExecutionProcessor,
    CalendarExecutionRetry,
    DynamoCalendarExecutionStore,
)
from quietpilot_worker.idempotency import DynamoIdempotencyStore

moto = pytest.importorskip("moto")
USER = "offline-user"
CASE = "case-approved"
EVIDENCE = "gmail:offline-evidence"
NOW = datetime(2026, 9, 14, tzinfo=UTC).timestamp()
TABLE = "calendar-worker-test"


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def marshal(value: dict[str, Any]) -> dict[str, Any]:
    return {key: TypeSerializer().serialize(item) for key, item in value.items()}


class Runtime:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.before_return = lambda: None
        self.error: Exception | None = None

    def invoke_payload(
        self, user_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((user_id, copy.deepcopy(payload)))
        self.before_return()
        if self.error:
            raise self.error
        return copy.deepcopy(self.result)


class Fixture:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.now = NOW
        self.account = hashlib.sha256(b"offline-account").hexdigest()
        self.action = {
            "action_id": "action-calendar-1",
            "connector": "google",
            "target": "primary",
            "verb": "calendar_event_create",
            "label": "일정 등록",
            "risk": "MEDIUM",
            "reversible": True,
            "required_scopes": [CALENDAR_SCOPE],
            "status": "PROPOSED",
            "result_summary": None,
            "parameters": {
                "summary": "승인된 행사",
                "start": "2026-09-20T09:00:00+09:00",
                "end": "2026-09-20T10:00:00+09:00",
                "timeZone": "Asia/Seoul",
                "source_ref": EVIDENCE,
            },
        }
        material = {
            "case_id": CASE,
            "version": 1,
            "evidence_revisions": {EVIDENCE: 3},
            "actions": [
                {
                    key: self.action[key]
                    for key in (
                        "connector",
                        "target",
                        "verb",
                        "parameters",
                        "required_scopes",
                        "risk",
                        "reversible",
                    )
                }
            ],
            "risk": "MEDIUM",
        }
        self.plan_hash = digest(material)
        self.operation_id = hashlib.sha256(
            f"{USER}:{CASE}:1:{self.plan_hash}".encode()
        ).hexdigest()
        self.approval_id = hashlib.sha256(f"{self.operation_id}:7".encode()).hexdigest()
        self.plan = {
            "version": 1,
            "hash": self.plan_hash,
            "evidence_revisions": {EVIDENCE: 3},
            "actions": [self.action],
            "required_scopes": [CALENDAR_SCOPE],
            "risk": "MEDIUM",
        }
        self.put(
            "META",
            {
                "entity_type": "case",
                "case_id": CASE,
                "user_id": USER,
                "status": "QUEUED",
                "risk": "MEDIUM",
                "version": 8,
                "current_plan_hash": self.plan_hash,
                "current_plan_version": 1,
                "requested_plan_version": 1,
                "evidence_refs": [EVIDENCE],
                "approved_operation_id": self.operation_id,
                "approved_approval_id": self.approval_id,
            },
        )
        self.put(
            "PLAN#000001",
            {
                "entity_type": "plan",
                "plan_hash": self.plan_hash,
                "version": 1,
                "plan_json": json.dumps(self.plan, ensure_ascii=False),
            },
        )
        self.put(
            "EVIDENCE#1",
            {
                "entity_type": "case_evidence",
                "user_id": USER,
                "evidence_ref": EVIDENCE,
                "revision": 3,
            },
        )
        self.put(
            f"APPROVAL#{self.approval_id}",
            {
                "entity_type": "approval",
                "user_id": USER,
                "operation_id": self.operation_id,
                "approval_id": self.approval_id,
                "plan_hash": self.plan_hash,
                "plan_version": 1,
                "expected_case_version": 7,
                "grant_mode": "ONCE",
                "decision": "APPROVE",
                "expires_at": datetime.fromtimestamp(NOW + 3600, UTC).isoformat(),
                "created_at": datetime.fromtimestamp(NOW, UTC).isoformat(),
                "mail_connection_id": "mail-epoch-1",
                "account_hash": self.account,
                "action_json": json.dumps(self.action, ensure_ascii=False),
            },
        )
        for provider, scope in (
            ("google", GMAIL_SCOPE),
            ("google-calendar", CALENDAR_SCOPE),
        ):
            self.put(
                f"CONNECTION#{provider}",
                {
                    "entity_type": "connection",
                    "user_id": USER,
                    "status": "CONNECTED",
                    "granted_scopes": [scope],
                    "mail_connection_id": "mail-epoch-1",
                    "account_hash": self.account,
                },
                user=True,
            )
        event_id = "qp" + digest(
            {"operation": "calendar.event_create.v1", "id": self.operation_id}
        )
        self.completed = {
            "status": "COMPLETED",
            "verified": True,
            "result_ref": f"google-calendar:primary:{event_id}",
            "html_url": "https://www.google.com/calendar/event?eid=offline",
            "error_code": None,
        }
        self.runtime = Runtime(self.completed)
        self.store = DynamoCalendarExecutionStore(TABLE, client, clock=lambda: self.now)
        self.processor = CalendarExecutionProcessor(self.runtime, self.store)
        self.envelope = {
            "schema_version": 1,
            "event_id": "offline-event",
            "event_type": "ACTION_EXECUTE_REQUESTED",
            "user_id": USER,
            "connector": "google",
            "occurred_at": "2026-09-14T00:00:00Z",
            "dedupe_key": "calendar:approved-operation",
            "trace_id": "offline-trace",
            "payload": {"case_id": CASE, "operation_id": self.operation_id},
        }

    def put(self, suffix: str, values: dict[str, Any], *, user: bool = False) -> None:
        item = {
            "PK": f"USER#{USER}" if user else f"CASE#{CASE}",
            "SK": suffix,
            **values,
        }
        self.client.put_item(TableName=TABLE, Item=marshal(item))

    def read(self, suffix: str, *, user: bool = False) -> dict[str, Any]:
        item = self.client.get_item(
            TableName=TABLE,
            Key=marshal(
                {"PK": f"USER#{USER}" if user else f"CASE#{CASE}", "SK": suffix}
            ),
            ConsistentRead=True,
        ).get("Item", {})
        return {
            key: TypeDeserializer().deserialize(value) for key, value in item.items()
        }

    def change(
        self, suffix: str, values: dict[str, Any], *, user: bool = False
    ) -> None:
        self.put(suffix, {**self.read(suffix, user=user), **values}, user=user)

    def action_record(self) -> dict[str, Any]:
        return self.read(f"ACTION#{self.operation_id}")

    def notifications(self) -> list[dict[str, Any]]:
        items = self.client.query(
            TableName=TABLE,
            KeyConditionExpression="PK=:pk AND begins_with(SK,:prefix)",
            ExpressionAttributeValues={
                ":pk": {"S": f"USER#{USER}"},
                ":prefix": {"S": "NOTIFICATION#"},
            },
            ConsistentRead=True,
        )["Items"]
        return [
            {key: TypeDeserializer().deserialize(value) for key, value in item.items()}
            for item in items
        ]

    def renew_approval(self) -> None:
        previous = self.read(f"APPROVAL#{self.approval_id}")
        version = int(self.read("META")["version"])
        self.approval_id = hashlib.sha256(
            f"{self.operation_id}:{version}".encode()
        ).hexdigest()
        self.put(
            f"APPROVAL#{self.approval_id}",
            {
                **previous,
                "SK": f"APPROVAL#{self.approval_id}",
                "approval_id": self.approval_id,
                "expected_case_version": version,
                "created_at": datetime.fromtimestamp(self.now, UTC).isoformat(),
                "expires_at": datetime.fromtimestamp(self.now + 3600, UTC).isoformat(),
            },
        )
        self.change(
            "META",
            {
                "approved_approval_id": self.approval_id,
                "version": version + 1,
                "status": "QUEUED",
            },
        )


@pytest.fixture
def fixture(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield Fixture(client)


def test_verified_execution_preserves_immutable_plan_and_approval_and_replays_once(
    fixture: Fixture,
) -> None:
    before = fixture.read("PLAN#000001")
    approval = fixture.read(f"APPROVAL#{fixture.approval_id}")

    def during() -> None:
        assert fixture.read("META")["status"] == "RUNNING"
        assert fixture.action_record()["status"] == "RUNNING"

    fixture.runtime.before_return = during
    fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == "COMPLETED"
    assert fixture.read("META")["GSI1PK"] == f"USER#{USER}#CASE#HISTORY"
    action = fixture.action_record()
    assert action["status"] == "SUCCEEDED" and action["verified"] is True
    assert action["action_id"] == fixture.action["action_id"]
    assert (
        action["plan_version"] == 1 and action["operation_id"] == fixture.operation_id
    )
    assert action["result_ref"] == fixture.completed["result_ref"]
    assert action["html_url"] == fixture.completed["html_url"]
    assert fixture.read("PLAN#000001") == before
    assert fixture.read(f"APPROVAL#{fixture.approval_id}") == approval
    invocation = fixture.runtime.calls[0][1]
    assert invocation == {
        "operation": "GOOGLE_CALENDAR_EXECUTE",
        "user_id": USER,
        "operation_id": fixture.operation_id,
        "account_hash": fixture.account,
        "parameters": {
            key: value
            for key, value in fixture.action["parameters"].items()
            if key != "source_ref"
        },
    }
    fixture.processor.process(fixture.envelope)
    assert len(fixture.runtime.calls) == 1
    assert fixture.notifications() == []


def test_concurrent_process_does_not_obtain_another_execution_lease(
    fixture: Fixture,
) -> None:
    duplicate_runtime = Runtime(fixture.completed)
    duplicate = CalendarExecutionProcessor(
        duplicate_runtime,
        DynamoCalendarExecutionStore(TABLE, fixture.client, clock=lambda: fixture.now),
    )

    def concurrently() -> None:
        with pytest.raises(CalendarExecutionRetry):
            duplicate.process(fixture.envelope)

    fixture.runtime.before_return = concurrently
    fixture.processor.process(fixture.envelope)
    assert not duplicate_runtime.calls
    assert fixture.action_record()["attempt_count"] == 1


def test_unknown_result_keeps_lease_and_recovers_with_the_same_operation(
    fixture: Fixture,
) -> None:
    fixture.runtime.error = TimeoutError("sensitive transport context")
    with pytest.raises(CalendarExecutionRetry):
        fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == "VERIFYING"
    assert fixture.action_record()["verified"] is False
    assert fixture.action_record()["lease_until"] == NOW + 150
    with pytest.raises(CalendarExecutionRetry):
        fixture.processor.process(fixture.envelope)
    assert len(fixture.runtime.calls) == 1
    fixture.now += 151
    fixture.runtime.error = None
    fixture.processor.process(fixture.envelope)
    assert fixture.runtime.calls[0] == fixture.runtime.calls[1]
    assert fixture.read("META")["status"] == "COMPLETED"
    assert fixture.action_record()["attempt_count"] == 2
    assert "sensitive" not in str(fixture.action_record())


def test_expired_crashed_claim_can_be_recovered_but_old_owner_cannot_finish(
    fixture: Fixture,
) -> None:
    first = fixture.store.claim(USER, CASE, fixture.operation_id)
    assert first is not None
    fixture.now += 151
    second = fixture.store.claim(USER, CASE, fixture.operation_id)
    assert second is not None
    with pytest.raises(CalendarExecutionLeaseLost):
        fixture.store.finish(first, fixture.completed)
    assert fixture.action_record()["lease_token"] == second.token
    fixture.store.finish(second, fixture.completed)
    assert fixture.read("META")["status"] == "COMPLETED"


@pytest.mark.parametrize("change", ["stop", "new_plan"])
def test_in_flight_result_is_recorded_without_overwriting_new_user_decisions(
    fixture: Fixture, change: str
) -> None:
    def update_case() -> None:
        meta = fixture.read("META")
        values = {
            "status": "STOPPED" if change == "stop" else "PREPARING",
            "version": int(meta["version"]) + 1,
        }
        if change == "new_plan":
            values.update(requested_plan_version=2, current_plan_hash="b" * 64)
        fixture.change("META", values)

    fixture.runtime.before_return = update_case
    fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == (
        "STOPPED" if change == "stop" else "PREPARING"
    )
    assert fixture.action_record()["status"] == "SUCCEEDED"
    assert fixture.action_record()["verified"] is True
    fixture.processor.process(fixture.envelope)
    assert len(fixture.runtime.calls) == 1
    assert fixture.notifications() == []


@pytest.mark.parametrize(
    ("suffix", "values", "user", "expected_status"),
    [
        ("META", {"status": "STOPPED"}, False, "STOPPED"),
        ("META", {"requested_plan_version": 2}, False, "DECISION_REQUIRED"),
        ("META", {"version": 7}, False, "DECISION_REQUIRED"),
        ("EVIDENCE#1", {"revision": 4}, False, "DECISION_REQUIRED"),
        ("EVIDENCE#1", {"user_id": "another-owner"}, False, "DECISION_REQUIRED"),
        ("CONNECTION#google", {"status": "REVOKING"}, True, "PERMISSION_REVOKED"),
        (
            "CONNECTION#google",
            {"mail_connection_id": "new-epoch"},
            True,
            "PERMISSION_REVOKED",
        ),
        (
            "CONNECTION#google-calendar",
            {"account_hash": "f" * 64},
            True,
            "PERMISSION_REVOKED",
        ),
        (
            "CONNECTION#google-calendar",
            {"granted_scopes": []},
            True,
            "PERMISSION_REVOKED",
        ),
    ],
)
def test_changed_approval_context_never_invokes_runtime(
    fixture: Fixture,
    suffix: str,
    values: dict[str, Any],
    user: bool,
    expected_status: str,
) -> None:
    fixture.change(suffix, values, user=user)
    fixture.processor.process(fixture.envelope)
    assert not fixture.runtime.calls
    assert fixture.read("META")["status"] == expected_status
    events = fixture.notifications()
    if expected_status == "STOPPED":
        assert events == []
    else:
        assert len(events) == 1
        assert events[0]["version"] == fixture.read("META")["version"]
        assert events[0]["case_id"] == CASE and events[0]["user_id"] == USER
        assert events[0]["kind"] == (
            "ACTION_FAILED"
            if expected_status == "PERMISSION_REVOKED"
            else "PLAN_CHANGED"
            if suffix == "EVIDENCE#1" or "requested_plan_version" in values
            else "DECISION_REQUIRED"
        )
        fixture.processor.process(fixture.envelope)
        assert fixture.notifications() == events


@pytest.mark.parametrize(
    "boundary",
    ["missing_approval", "wrong_owner", "wrong_operation", "standing", "expired"],
)
def test_missing_or_invalid_grant_never_executes(
    fixture: Fixture, boundary: str
) -> None:
    envelope = copy.deepcopy(fixture.envelope)
    if boundary == "missing_approval":
        fixture.client.delete_item(
            TableName=TABLE,
            Key=marshal(
                {"PK": f"CASE#{CASE}", "SK": f"APPROVAL#{fixture.approval_id}"}
            ),
        )
    elif boundary == "wrong_owner":
        envelope["user_id"] = "another-owner"
    elif boundary == "wrong_operation":
        envelope["payload"]["operation_id"] = "f" * 64
    elif boundary == "standing":
        fixture.change(f"APPROVAL#{fixture.approval_id}", {"grant_mode": "STANDING"})
    else:
        fixture.now += 3601
    fixture.processor.process(envelope)
    assert not fixture.runtime.calls


@pytest.mark.parametrize("change", ["plan_json", "approval_action", "evidence_hash"])
def test_original_plan_material_and_approval_action_must_agree(
    fixture: Fixture, change: str
) -> None:
    plan = copy.deepcopy(fixture.plan)
    if change == "plan_json":
        plan["actions"][0]["parameters"]["summary"] = "modified but not approved"
    elif change == "evidence_hash":
        plan["evidence_revisions"][EVIDENCE] = 4
    else:
        action = copy.deepcopy(fixture.action)
        action["target"] = "another-calendar"
        fixture.change(
            f"APPROVAL#{fixture.approval_id}", {"action_json": json.dumps(action)}
        )
    fixture.change("PLAN#000001", {"plan_json": json.dumps(plan)})
    fixture.processor.process(fixture.envelope)
    assert not fixture.runtime.calls
    assert fixture.read("META")["status"] == "DECISION_REQUIRED"


@pytest.mark.parametrize("change", ["stop", "revoke", "plan"])
def test_transaction_rechecks_authorization_after_the_reads(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    transact = fixture.client.transact_write_items
    modified = False

    def write(**kwargs: Any) -> Any:
        nonlocal modified
        if not modified:
            modified = True
            if change == "stop":
                fixture.change("META", {"status": "STOPPED", "version": 9})
            elif change == "revoke":
                fixture.change(
                    "CONNECTION#google-calendar", {"status": "DISCONNECTED"}, user=True
                )
            else:
                fixture.change("PLAN#000001", {"plan_json": "{}"})
        return transact(**kwargs)

    monkeypatch.setattr(fixture.client, "transact_write_items", write)
    with pytest.raises(CalendarExecutionRetry):
        fixture.processor.process(fixture.envelope)
    assert not fixture.runtime.calls
    assert not fixture.action_record()


@pytest.mark.parametrize("change", ["expiry", "permission"])
def test_revocation_after_unknown_result_preserves_the_unconfirmed_action(
    fixture: Fixture, change: str
) -> None:
    fixture.runtime.error = TimeoutError()
    with pytest.raises(CalendarExecutionRetry):
        fixture.processor.process(fixture.envelope)
    before = fixture.action_record()
    if change == "expiry":
        fixture.now += 3601
    else:
        fixture.change("CONNECTION#google-calendar", {"granted_scopes": []}, user=True)
    fixture.processor.process(fixture.envelope)
    assert len(fixture.runtime.calls) == 1
    assert fixture.action_record() == before
    assert fixture.read("META")["status"] == (
        "DECISION_REQUIRED" if change == "expiry" else "PERMISSION_REVOKED"
    )


@pytest.mark.parametrize(
    "result",
    [
        {"status": "COMPLETED", "verified": False},
        {"status": "COMPLETED", "verified": "true"},
        {"status": "ACCEPTED", "verified": True},
        {"status": "COMPLETED", "verified": True, "result_ref": "another-provider:1"},
    ],
)
def test_runtime_acceptance_or_invalid_response_is_not_completion(
    fixture: Fixture, result: dict[str, object]
) -> None:
    fixture.runtime.result = result
    with pytest.raises(CalendarExecutionRetry):
        fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == "VERIFYING"
    assert fixture.action_record()["verified"] is False


def test_explicit_executor_failure_is_persisted_without_automatic_reexecution(
    fixture: Fixture,
) -> None:
    fixture.runtime.result = {
        "status": "FAILED",
        "verified": False,
        "error_code": "CALENDAR_READBACK_MISMATCH",
        "result_ref": None,
        "html_url": None,
    }
    fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == "FAILED"
    assert fixture.action_record()["error_code"] == "CALENDAR_READBACK_MISMATCH"
    events = fixture.notifications()
    assert len(events) == 1
    assert events[0]["kind"] == "ACTION_FAILED"
    assert events[0]["version"] == fixture.read("META")["version"] == 10
    fixture.processor.process(fixture.envelope)
    assert len(fixture.runtime.calls) == 1
    assert fixture.notifications() == events


@pytest.mark.parametrize("change", ["stop", "new_plan"])
def test_failed_in_flight_result_preserves_new_decision_without_stale_notification(
    fixture, change
):
    fixture.runtime.result = {
        "status": "FAILED",
        "verified": False,
        "error_code": "CALENDAR_READBACK_MISMATCH",
        "result_ref": None,
        "html_url": None,
    }

    def update_case():
        values = {
            "status": "STOPPED" if change == "stop" else "PREPARING",
            "version": int(fixture.read("META")["version"]) + 1,
        }
        if change == "new_plan":
            values["requested_plan_version"] = 2
        fixture.change("META", values)

    fixture.runtime.before_return = update_case
    fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == (
        "STOPPED" if change == "stop" else "PREPARING"
    )
    assert fixture.action_record()["status"] == "FAILED"
    assert fixture.notifications() == []


@pytest.mark.parametrize(
    "error_code", ["GOOGLE_AUTH_REQUIRED", "GOOGLE_ACCOUNT_CHANGED"]
)
def test_executor_permission_failure_enqueues_only_with_current_attention_state(
    fixture, error_code
):
    fixture.runtime.result = {
        "status": "FAILED",
        "verified": False,
        "error_code": error_code,
        "result_ref": None,
        "html_url": None,
    }
    fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == "PERMISSION_REVOKED"
    events = fixture.notifications()
    assert len(events) == 1 and events[0]["kind"] == "ACTION_FAILED"
    assert events[0]["version"] == fixture.read("META")["version"] == 10
    fixture.processor.process(fixture.envelope)
    assert fixture.notifications() == events and len(fixture.runtime.calls) == 1


def test_block_notification_failure_leaves_case_unchanged_atomically(
    fixture, monkeypatch
):
    fixture.change("EVIDENCE#1", {"revision": 4})
    transact = fixture.client.transact_write_items

    def reject_notification(**kwargs):
        for operation in kwargs["TransactItems"]:
            put = operation.get("Put", {})
            if put.get("Item", {}).get("entity_type") == {"S": "notification"}:
                put["ConditionExpression"] = "attribute_exists(PK)"
        return transact(**kwargs)

    monkeypatch.setattr(fixture.client, "transact_write_items", reject_notification)
    fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == "QUEUED"
    assert fixture.read("META")["version"] == 8
    assert fixture.notifications() == [] and fixture.runtime.calls == []


def test_stop_between_block_read_and_transaction_prevents_attention_and_notification(
    fixture, monkeypatch
):
    fixture.change("EVIDENCE#1", {"revision": 4})
    transact = fixture.client.transact_write_items

    def stop_before_commit(**kwargs):
        fixture.change("META", {"status": "STOPPED", "version": 9})
        return transact(**kwargs)

    monkeypatch.setattr(fixture.client, "transact_write_items", stop_before_commit)
    fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == "STOPPED"
    assert fixture.notifications() == [] and fixture.runtime.calls == []


def test_verified_response_must_belong_to_this_operation(fixture: Fixture) -> None:
    fixture.runtime.result = {
        **fixture.completed,
        "result_ref": "google-calendar:primary:qp" + "a" * 64,
    }
    with pytest.raises(CalendarExecutionRetry):
        fixture.processor.process(fixture.envelope)
    assert fixture.action_record()["verified"] is False
    assert fixture.read("META")["status"] == "VERIFYING"


def test_unknown_result_is_not_acknowledged_by_existing_sqs_idempotency(
    fixture: Fixture,
) -> None:
    fixture.client.create_table(
        TableName="execution-idempotency",
        KeySchema=[
            {"AttributeName": "subject", "KeyType": "HASH"},
            {"AttributeName": "idempotencyKey", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "subject", "AttributeType": "S"},
            {"AttributeName": "idempotencyKey", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    gate = DynamoIdempotencyStore(
        "execution-idempotency",
        fixture.client,
        in_progress_ttl_seconds=150,
        clock=lambda: fixture.now,
    )
    event = {
        "Records": [{"messageId": "delivery", "body": json.dumps(fixture.envelope)}]
    }
    fixture.runtime.error = TimeoutError()
    first = consumer.handle_sqs_batch(
        event, fixture.processor.process, idempotency=gate
    )
    assert first == {"batchItemFailures": [{"itemIdentifier": "delivery"}]}
    fixture.now += 151
    fixture.runtime.error = None
    assert consumer.handle_sqs_batch(
        event, fixture.processor.process, idempotency=gate
    ) == {"batchItemFailures": []}
    assert consumer.handle_sqs_batch(
        event, fixture.processor.process, idempotency=gate
    ) == {"batchItemFailures": []}
    assert len(fixture.runtime.calls) == 2


def test_queue_handoff_retry_can_advance_case_version_before_the_first_action(
    fixture: Fixture,
) -> None:
    fixture.change("META", {"version": 10})
    fixture.processor.process(fixture.envelope)
    assert fixture.action_record()["status"] == "SUCCEEDED"
    assert len(fixture.runtime.calls) == 1


def test_expired_unknown_operation_uses_a_new_approval_but_the_same_event_identity(
    fixture: Fixture,
) -> None:
    old_id = fixture.approval_id
    old_approval = fixture.read(f"APPROVAL#{old_id}")
    fixture.runtime.error = TimeoutError()
    with pytest.raises(CalendarExecutionRetry):
        fixture.processor.process(fixture.envelope)
    fixture.now += 3601
    fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == "DECISION_REQUIRED"
    fixture.renew_approval()
    fixture.runtime.error = None
    fixture.processor.process(fixture.envelope)
    assert fixture.runtime.calls[0] == fixture.runtime.calls[1]
    assert fixture.action_record()["approval_id"] == fixture.approval_id != old_id
    assert fixture.action_record()["status"] == "SUCCEEDED"
    assert fixture.read(f"APPROVAL#{old_id}") == old_approval


def test_late_success_under_a_previous_approval_is_promoted_without_another_runtime_call(
    fixture: Fixture,
) -> None:
    previous_id = fixture.approval_id
    fixture.runtime.before_return = fixture.renew_approval
    fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == "QUEUED"
    assert fixture.action_record()["status"] == "SUCCEEDED"
    assert fixture.action_record()["approval_id"] == previous_id
    fixture.processor.process(fixture.envelope)
    assert fixture.read("META")["status"] == "COMPLETED"
    assert len(fixture.runtime.calls) == 1


def test_approval_pointer_change_after_reads_prevents_claim(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    transact = fixture.client.transact_write_items
    modified = False

    def write(**kwargs: Any) -> Any:
        nonlocal modified
        if not modified:
            modified = True
            fixture.renew_approval()
        return transact(**kwargs)

    monkeypatch.setattr(fixture.client, "transact_write_items", write)
    with pytest.raises(CalendarExecutionRetry):
        fixture.processor.process(fixture.envelope)
    assert not fixture.runtime.calls
    assert not fixture.action_record()


def test_consumer_dispatches_only_the_calendar_execute_event(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakeups = []
    monkeypatch.setattr(consumer, "_queue_case_notifications", wakeups.append)
    monkeypatch.setattr(
        consumer, "default_calendar_execution_processor", lambda: fixture.processor
    )
    monkeypatch.setattr(
        consumer,
        "default_google_job_processor",
        lambda: pytest.fail("Calendar work reached proposal-only Google jobs"),
    )
    consumer._foundation_processor(fixture.envelope)
    assert fixture.action_record()["verified"] is True
    assert wakeups == [fixture.envelope]
