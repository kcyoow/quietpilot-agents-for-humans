import copy
import json
from types import SimpleNamespace

import pytest
from quietpilot_control_api.connections import SqsWorkQueue
from quietpilot_worker.consumer import handle_sqs_batch
from quietpilot_worker.idempotency import DynamoIdempotencyStore
from quietpilot_worker.mail_jobs import (
    DynamoMailJobStore,
    MailJobProcessor,
    SqsMailQueue,
    StaleMailJob,
)

from services.worker.tests.test_idempotency import FakeDynamoDb
from services.worker.tests.test_mail_jobs import IDENTITY, Store, snapshot


class RecoveryStore(Store):
    recover_setup = DynamoMailJobStore.recover_setup
    guard = DynamoMailJobStore.guard

    def __init__(self, kind, crash=None):
        super().__init__()
        self.table = "mail-table"
        self.kind = kind
        self.crash = crash
        self.transactions = []
        self.setup_epochs = []
        self.connection.update(
            status={"S": "ERROR"},
            error_code={"S": "MAIL_SETUP_FAILED"},
            granted_scopes={
                "L": [{"S": "https://www.googleapis.com/auth/gmail.readonly"}]
            },
        )

    def transact(self, updates):
        self.transactions.append(copy.deepcopy(updates))
        connection, mail = [update["Update"] for update in updates]
        assert connection["Key"]["PK"]["S"] == mail["Key"]["PK"]["S"] == "USER#owner"
        values = connection["ExpressionAttributeValues"]
        self.connection.update(
            status=values[":connecting"],
            mail_connection_id=values[":new"],
            gmail_scan_id=values[":new"],
        )
        values = mail["ExpressionAttributeValues"]
        self.item[f"{self.kind}_connection_id"] = values[":new"]
        self.item["mail_recovery_json"] = values[":recovery"]
        if self.crash == "after_marker":
            self.crash = None
            raise SystemExit("worker stopped after recovery commit")

    def setup(self, user_id, epoch, result, *, expected_status="CONNECTING"):
        assert user_id == "owner"
        if (
            self.connection["mail_connection_id"]["S"] != epoch
            or self.connection["status"]["S"] != expected_status
        ):
            raise StaleMailJob
        self.setup_epochs.append(epoch)
        self.connection["status"] = {"S": "CONNECTED"}
        if self.crash == "after_connected":
            self.crash = None
            raise SystemExit("worker stopped after connected commit")

    def setup_failed(self, user_id, epoch, *, expected_status="CONNECTING"):
        assert user_id == "owner"
        assert self.connection["mail_connection_id"]["S"] == epoch
        assert self.connection["status"]["S"] == expected_status
        self.connection["status"] = {"S": "ERROR"}

    def ensure_job(self, user_id, kind):
        assert user_id == "owner"
        if kind != self.kind or self.state[kind]["status"] == "READY":
            return None
        field = "scan_id" if kind == "scan" else "request_id"
        if self.state[kind]["status"] == "ERROR":
            self.state[kind].update(status="PENDING", **{field: "b" * 32})
        return {
            "request_id": self.state[kind][field],
            "profile_version": self.state["profile"]["version"],
        }

    def persist(self, *args, **kwargs):
        super().persist(*args, **kwargs)
        self.state["scan"]["status"] = "READY"
        self.item["scan_step"] = {"N": str(kwargs["step"] + 1)}
        self.item["scan_next_json"] = {"S": json.dumps(kwargs["next_work"])}

    def finish_recommendations(self, *args):
        super().finish_recommendations(*args)
        self.state["recommendations"]["status"] = "READY"


class Runtime:
    def __init__(self):
        self.calls = []
        self.setup_result = {"status": "CONNECTED"}
        self.setup_error = None

    def invoke(self, user_id, operation, parameters=None):
        assert user_id == "owner"
        self.calls.append(operation)
        if operation == "GOOGLE_MAIL_SETUP":
            if self.setup_error is not None:
                error, self.setup_error = self.setup_error, None
                raise error
            return self.setup_result
        if operation == "GOOGLE_INTEREST_TAGS":
            return {
                "status": "INTEREST_TAGS",
                "tags": [],
                "title_count": 0,
                "generated_at": "2026-09-12T00:00:00Z",
            }
        assert operation == "GOOGLE_INTEREST_SCAN"
        return snapshot()


def harness(kind, *, crash=None, producer="worker"):
    store = RecoveryStore(kind, crash)
    runtime = Runtime()
    messages = []
    sqs = SimpleNamespace(
        send_message=lambda **values: messages.append(json.loads(values["MessageBody"]))
    )
    queue = SqsMailQueue("mail-queue", sqs)
    origin_queue = queue if producer == "worker" else SqsWorkQueue("mail-queue", sqs)
    event = (
        "MAIL_SCAN_REQUESTED" if kind == "scan" else "MAIL_RECOMMENDATIONS_REQUESTED"
    )
    origin_queue.send(
        user_id="owner",
        event_type=event,
        payload={"request_id": IDENTITY, "profile_version": 1},
    )
    original = messages.pop()
    worker = MailJobProcessor(
        runtime,
        SimpleNamespace(claim_history_sync=lambda *args, **kwargs: None),
        store,
        queue,
        SimpleNamespace(),
    )
    now = [1000]
    gate = DynamoIdempotencyStore(
        "dedupe-table", FakeDynamoDb(), in_progress_ttl_seconds=30, clock=lambda: now[0]
    )

    def deliver(envelope=original):
        return handle_sqs_batch(
            {"Records": [{"messageId": "delivery", "body": json.dumps(envelope)}]},
            worker.process,
            idempotency=gate,
        )

    return store, runtime, messages, original, now, deliver


@pytest.mark.parametrize("kind", ["scan", "recommendations"])
@pytest.mark.parametrize("producer", ["worker", "api"])
def test_recovery_runs_mail_before_the_original_dedupe_record_completes(kind, producer):
    store, runtime, messages, original, _, deliver = harness(kind, producer=producer)

    assert deliver() == {"batchItemFailures": []}
    assert store.state[kind]["status"] == "READY"
    main_operation = (
        "GOOGLE_INTEREST_SCAN" if kind == "scan" else "GOOGLE_INTEREST_TAGS"
    )
    assert runtime.calls == ["GOOGLE_MAIL_SETUP", main_operation]
    duplicate = next(
        item for item in messages if item["event_type"] == original["event_type"]
    )
    assert (duplicate["dedupe_key"] == original["dedupe_key"]) == (producer == "worker")
    assert deliver(duplicate) == {"batchItemFailures": []}
    assert runtime.calls == ["GOOGLE_MAIL_SETUP", main_operation]


@pytest.mark.parametrize("kind", ["scan", "recommendations"])
@pytest.mark.parametrize("crash", ["after_marker", "after_connected"])
def test_interrupted_recovery_resumes_the_same_epoch_and_completes_mail(kind, crash):
    store, runtime, _, _, now, deliver = harness(kind, crash=crash)
    with pytest.raises(SystemExit):
        deliver()
    epoch = store.connection["mail_connection_id"]["S"]
    now[0] += 31

    assert deliver() == {"batchItemFailures": []}
    assert store.connection["mail_connection_id"]["S"] == epoch
    assert store.state[kind]["status"] == "READY"
    assert len(store.transactions) == 1
    assert runtime.calls.count("GOOGLE_MAIL_SETUP") == 1


@pytest.mark.parametrize("kind", ["scan", "recommendations"])
def test_setup_failure_retries_without_rotating_the_recovery_generation(kind):
    store, runtime, _, _, _, deliver = harness(kind)
    runtime.setup_error = RuntimeError("temporary setup failure")
    assert deliver() == {"batchItemFailures": [{"itemIdentifier": "delivery"}]}
    epoch = store.connection["mail_connection_id"]["S"]

    assert deliver() == {"batchItemFailures": []}
    assert store.connection["mail_connection_id"]["S"] == epoch
    assert store.state[kind]["status"] == "READY"
    assert len(store.transactions) == 1


@pytest.mark.parametrize("kind", ["scan", "recommendations"])
def test_normal_oauth_connecting_never_begins_inline_recovery(kind):
    store, runtime, messages, _, _, deliver = harness(kind)
    store.connection["status"] = {"S": "CONNECTING"}
    assert deliver() == {"batchItemFailures": []}
    assert not runtime.calls and not store.transactions and not messages


@pytest.mark.parametrize(
    "change", ["kind", "request_id", "profile_version", "connection_id"]
)
def test_recovery_marker_must_match_the_original_event_and_current_epoch(change):
    store, runtime, messages, _, now, deliver = harness("scan", crash="after_marker")
    with pytest.raises(SystemExit):
        deliver()
    marker = json.loads(store.item["mail_recovery_json"]["S"])
    marker[change] = 2 if change == "profile_version" else "different"
    store.item["mail_recovery_json"] = {"S": json.dumps(marker)}
    now[0] += 31

    assert deliver() == {"batchItemFailures": []}
    assert not runtime.calls and not messages
    assert len(store.transactions) == 1


def test_profile_edit_during_recovery_queues_the_latest_scan_without_running_old_profile():
    store, runtime, messages, _, now, deliver = harness("scan", crash="after_marker")
    with pytest.raises(SystemExit):
        deliver()
    store.state["profile"].update(version=2, tags=["장학금"])
    store.state["scan"].update(scan_id="b" * 32, profile_version=2)
    now[0] += 31

    assert deliver() == {"batchItemFailures": []}
    assert runtime.calls == ["GOOGLE_MAIL_SETUP"]
    assert store.state["profile"]["tags"] == ["장학금"]
    assert messages[0]["payload"] == {"request_id": "b" * 32, "profile_version": 2}


def test_setup_replacing_failed_origin_leaves_a_distinct_durable_mail_job():
    store, runtime, messages, original, now, deliver = harness(
        "recommendations", crash="after_connected"
    )
    with pytest.raises(SystemExit):
        deliver()
    store.state["recommendations"]["status"] = "ERROR"
    now[0] += 31

    assert deliver() == {"batchItemFailures": []}
    followup = messages[0]
    assert followup["payload"]["request_id"] == "b" * 32
    assert followup["dedupe_key"] != original["dedupe_key"]
    assert deliver(followup) == {"batchItemFailures": []}
    assert runtime.calls == ["GOOGLE_MAIL_SETUP", "GOOGLE_INTEREST_TAGS"]
    assert store.state["recommendations"]["status"] == "READY"


def test_authorization_required_remains_terminal_during_recovery():
    store, runtime, messages, _, _, deliver = harness("scan")
    runtime.setup_result = {
        "status": "AUTHORIZATION_REQUIRED",
        "error_code": "GOOGLE_AUTH_REQUIRED",
    }
    assert deliver() == {"batchItemFailures": []}
    assert store.connection["error_code"]["S"] == "GOOGLE_AUTH_REQUIRED"
    assert runtime.calls == ["GOOGLE_MAIL_SETUP"]
    assert not messages
