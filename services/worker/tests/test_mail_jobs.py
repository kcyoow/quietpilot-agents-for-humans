import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from quietpilot_worker.google_connection_store import (
    DynamoConnectionWriter,
    HistorySyncInProgress,
)
from quietpilot_worker.google_jobs import _scan_page_details, _sync_details
from quietpilot_worker.mail_jobs import (
    DynamoMailJobStore,
    MailJobProcessor,
    StaleMailJob,
)

IDENTITY = "a" * 32
REF = "gmail:" + "1" * 64
OTHER_REF = "gmail:" + "2" * 64


def state():
    return {
        "profile": {
            "tags": ["학교"],
            "description": "",
            "version": 1,
            "updated_at": None,
        },
        "recommendations": {
            "status": "PENDING",
            "request_id": IDENTITY,
            "tags": [],
            "title_count": 0,
            "generated_at": None,
            "error_code": None,
        },
        "scan": {
            "status": "PENDING",
            "scan_id": IDENTITY,
            "profile_version": 1,
            "processed_count": 0,
            "matched_count": 0,
            "completed_at": None,
            "error_code": None,
        },
    }


def evidence(ref=REF):
    return {
        "ref": ref,
        "source": "gmail",
        "revision": 1,
        "title": "학교 공지",
        "facts": ["sender_domain=school.example"],
        "untrusted_text": None,
    }


def snapshot():
    return {
        "status": "CONNECTED",
        "processed_message_count": 2,
        "recent_message_estimate": 2,
        "next_page_token": None,
        "completion_history_id": "110",
        "history_id": "100",
        "evidence": [evidence(), evidence(OTHER_REF)],
        "candidates": [],
        "unresolved_evidence_count": 0,
        "discovery_validated": True,
        "interest_profile_revision": 1,
        "interest_validated": True,
        "interest_matches": [
            {
                "evidence_ref": REF,
                "title": "학교 공지",
                "summary": "학교 소식이에요.",
                "reason": "학교 관심사와 관련돼요.",
                "sender_domain": "school.example",
                "received_at": None,
                "matched_tags": ["학교"],
            }
        ],
    }


class Runtime:
    def __init__(self, result=None):
        self.result = result if result is not None else snapshot()
        self.calls = []

    def invoke(self, *args):
        self.calls.append(args)
        return self.result


class Store:
    def __init__(self):
        self.state = state()
        self.item = {
            "scan_connection_id": {"S": "epoch"},
            "recommendations_connection_id": {"S": "epoch"},
            "scan_step": {"N": "0"},
        }
        self.connection = {
            "status": {"S": "CONNECTED"},
            "mail_connection_id": {"S": "epoch"},
        }
        self.persisted = []
        self.failures = []
        self.finished = []
        self.auth_errors = []

    def load(self, user_id):
        return copy.deepcopy(self.state), dict(self.item), dict(self.connection)

    def persist(self, *args, **kwargs):
        self.persisted.append((args, kwargs))

    def fail(self, *args):
        self.failures.append(args)

    def finish_recommendations(self, *args):
        self.finished.append(args)

    def authorization_required(self, user_id, epoch):
        if self.connection["mail_connection_id"]["S"] != epoch:
            return
        self.auth_errors.append((user_id, epoch))
        self.connection.update(
            status={"S": "ERROR"}, error_code={"S": "GOOGLE_AUTH_REQUIRED"}
        )
        for kind in ("recommendations", "scan"):
            self.state[kind].update(status="ERROR", error_code="GOOGLE_AUTH_REQUIRED")


def processor(store=None, runtime=None):
    store = store or Store()
    runtime = runtime or Runtime()
    queue = SimpleNamespace(calls=[])
    queue.send = lambda **args: queue.calls.append(args)
    writer = SimpleNamespace(claim_history_sync=lambda *args, **kwargs: None)
    legacy = SimpleNamespace(process=lambda envelope: None)
    return (
        MailJobProcessor(runtime, writer, store, queue, legacy),
        store,
        runtime,
        queue,
    )


def envelope(event="MAIL_SCAN_REQUESTED", **payload):
    return {
        "connector": "google",
        "user_id": "owner",
        "event_type": event,
        "payload": {"request_id": IDENTITY, "profile_version": 1, **payload},
    }


@pytest.mark.parametrize(
    "event", ["INITIAL_SCAN_REQUESTED", "INITIAL_SCAN_CONTINUATION"]
)
def test_legacy_broad_scan_jobs_never_invoke_runtime(event):
    worker, store, runtime, _ = processor()
    worker.process(envelope(event))
    assert not runtime.calls and not store.persisted


def test_old_revoke_cannot_disconnect_a_new_connection():
    worker, store, runtime, _ = processor()
    worker.process(envelope("GOOGLE_CONNECTION_REVOKED", request_id="b" * 32))
    assert not runtime.calls
    store.connection.update(status={"S": "REVOKING"}, gmail_scan_id={"S": "c" * 32})
    worker.process(envelope("GOOGLE_CONNECTION_REVOKED", request_id="b" * 32))
    assert not runtime.calls


@pytest.mark.parametrize(
    "event",
    ["MAIL_SCAN_REQUESTED", "MAIL_SCAN_CONTINUATION", "GMAIL_HISTORY_AVAILABLE"],
)
def test_unconfigured_interests_gate_all_discovery_entrypoints(event):
    worker, store, runtime, _ = processor()
    store.state["profile"].update(tags=[], description=" ")
    worker.process(envelope(event, history_id="110", page_token="page-2"))
    assert not runtime.calls and not store.persisted


@pytest.mark.parametrize("mutation", ["revision", "connection", "request"])
def test_stale_scan_jobs_return_before_runtime(mutation):
    worker, store, runtime, _ = processor()
    if mutation == "revision":
        store.state["profile"]["version"] = 2
    elif mutation == "connection":
        store.connection["mail_connection_id"] = {"S": "new-epoch"}
    else:
        store.state["scan"]["scan_id"] = "b" * 32
    worker.process(envelope())
    assert not runtime.calls


def test_scan_passes_saved_profile_and_preserves_informational_matches():
    worker, store, runtime, queue = processor()
    worker.process(envelope())
    assert runtime.calls == [
        (
            "owner",
            "GOOGLE_INTEREST_SCAN",
            {"interest_profile": {"revision": 1, "tags": ["학교"], "description": ""}},
        )
    ]
    assert store.persisted[0][0][4]["candidates"] == []
    assert len(store.persisted[0][0][4]["interest_matches"]) == 1
    assert queue.calls[0]["event_type"] == "GMAIL_HISTORY_AVAILABLE"
    assert queue.calls[0]["payload"]["history_id"] == "110"


@pytest.mark.parametrize("discovery_validated", [True, False])
@pytest.mark.parametrize("history", [True, False])
def test_interest_scan_keeps_matches_when_action_preparation_is_incomplete(
    discovery_validated, history
):
    result = snapshot()
    result.update(discovery_validated=discovery_validated, unresolved_evidence_count=1)
    if not discovery_validated:
        result["candidates"] = [{"unsafe": "must never reach storage"}]
    if history:
        result.update(
            status="SYNCED",
            history_id="110",
            recovery_mode="INCREMENTAL",
            continuation_required=False,
        )
    worker, store, _, _ = processor(runtime=Runtime(result))
    if history:
        store.state["scan"].update(status="READY", completed_at="2026-09-09T00:00:00Z")
        worker.writer.claim_history_sync = lambda *args, **kwargs: SimpleNamespace(
            start_history_id="100", token="lease"
        )
        worker.writer.release_history_sync = lambda *args, **kwargs: None
    worker.process(
        envelope(
            "GMAIL_HISTORY_AVAILABLE" if history else "MAIL_SCAN_REQUESTED",
            history_id="110",
        )
    )
    assert not store.failures and len(store.persisted) == 1
    args, _ = store.persisted[0]
    assert len(args[4]["interest_matches"]) == 1
    assert args[5]["candidates"] == []
    assert args[5]["action_preparation_incomplete"] is True


def test_partial_interest_mode_requires_validation_and_preserves_legacy_gate():
    result = snapshot()
    result.update(discovery_validated=False, unresolved_evidence_count=1)
    with pytest.raises(RuntimeError, match="discovery"):
        _scan_page_details(result)
    result["interest_validated"] = False
    with pytest.raises(RuntimeError, match="interest"):
        _scan_page_details(result, interest_results=True)
    result.update(
        status="SYNCED",
        history_id="110",
        recovery_mode="INCREMENTAL",
        continuation_required=False,
        discovery_validated=True,
    )
    with pytest.raises(RuntimeError, match="unresolved"):
        _sync_details(result)


def test_late_profile_change_cannot_publish_runtime_result():
    worker, store, runtime, queue = processor()

    def reject(*args, **kwargs):
        raise StaleMailJob

    store.persist = reject
    worker.process(envelope())
    assert len(runtime.calls) == 1
    assert not queue.calls and not store.failures


def test_recommendations_do_not_pass_profile_or_modify_manual_selection():
    runtime = Runtime(
        {
            "status": "INTEREST_TAGS",
            "tags": [],
            "title_count": 0,
            "sampled": False,
            "generated_at": "2026-09-09T00:00:00Z",
        }
    )
    worker, store, _, _ = processor(runtime=runtime)
    store.state["profile"].update(tags=["수동선택"], version=8)
    worker.process(envelope("MAIL_RECOMMENDATIONS_REQUESTED"))
    assert runtime.calls == [("owner", "GOOGLE_INTEREST_TAGS")]
    assert store.state["profile"]["tags"] == ["수동선택"]
    assert store.finished[0][1] == IDENTITY


def test_model_failure_sets_explicit_error_and_is_retryable():
    worker, store, runtime, _ = processor()

    def reject(*args):
        raise RuntimeError("MAIL_INTEREST_OUTPUT_INVALID")

    runtime.invoke = reject
    with pytest.raises(RuntimeError):
        worker.process(envelope())
    assert store.failures == [("owner", "scan", IDENTITY, "epoch")]


@pytest.mark.parametrize(
    "event",
    [
        "MAIL_RECOMMENDATIONS_REQUESTED",
        "MAIL_SCAN_REQUESTED",
        "MAIL_SCAN_CONTINUATION",
        "GMAIL_HISTORY_AVAILABLE",
    ],
)
def test_typed_auth_failure_is_terminal_and_not_an_inference_retry(event):
    worker, store, runtime, queue = processor(
        runtime=Runtime(
            {"status": "AUTHORIZATION_REQUIRED", "error_code": "GOOGLE_AUTH_REQUIRED"}
        )
    )
    if event == "GMAIL_HISTORY_AVAILABLE":
        store.state["scan"].update(status="READY", completed_at="2026-09-09T00:00:00Z")
        worker.writer.claim_history_sync = lambda *args, **kwargs: SimpleNamespace(
            start_history_id="100", token="lease"
        )
        worker.writer.release_history_sync = lambda *args, **kwargs: None
    work = envelope(event, page_token="next-page", history_id="110")
    worker.process(work)
    worker.process(work)
    assert len(runtime.calls) == 1
    assert store.auth_errors == [("owner", "epoch")]
    assert (
        store.state["recommendations"]["error_code"]
        == store.state["scan"]["error_code"]
        == "GOOGLE_AUTH_REQUIRED"
    )
    assert (
        not store.failures
        and not store.persisted
        and not store.finished
        and not queue.calls
    )
    assert store.state["profile"]["tags"] == ["학교"]


def test_setup_auth_error_is_not_rewritten_as_setup_failure():
    worker, store, runtime, queue = processor(
        runtime=Runtime(
            {"status": "AUTHORIZATION_REQUIRED", "error_code": "GOOGLE_AUTH_REQUIRED"}
        )
    )
    store.connection.update(
        status={"S": "CONNECTING"}, mail_connection_id={"S": IDENTITY}
    )
    store.setup = lambda *args, **kwargs: pytest.fail(
        "auth-required setup must not persist success"
    )
    store.setup_failed = lambda *args, **kwargs: pytest.fail(
        "auth failure must keep its typed error"
    )
    worker.process(envelope("MAIL_SETUP_REQUESTED", scan_id=IDENTITY))
    assert runtime.calls == [("owner", "GOOGLE_MAIL_SETUP")]
    assert store.auth_errors == [("owner", IDENTITY)]
    assert not queue.calls and not store.failures


def test_late_auth_result_does_not_mark_a_newly_reconnected_account():
    worker, store, runtime, queue = processor()

    def reconnect(*args):
        store.connection["mail_connection_id"] = {"S": "new-epoch"}
        return {
            "status": "AUTHORIZATION_REQUIRED",
            "error_code": "GOOGLE_AUTH_REQUIRED",
        }

    runtime.invoke = reconnect
    worker.process(envelope("MAIL_RECOMMENDATIONS_REQUESTED"))
    assert store.connection["status"]["S"] == "CONNECTED"
    assert not store.auth_errors and not store.failures and not queue.calls


def test_failed_incremental_sync_is_retried_after_an_initial_scan_completed():
    result = snapshot()
    result.update(
        status="SYNCED",
        history_id="110",
        recovery_mode="INCREMENTAL",
        continuation_required=False,
    )
    worker, store, runtime, _ = processor(runtime=Runtime(result))
    store.state["scan"].update(
        status="ERROR",
        completed_at="2026-09-09T00:00:00Z",
        error_code="MAIL_ANALYSIS_FAILED",
    )
    worker.writer.claim_history_sync = lambda *args, **kwargs: SimpleNamespace(
        start_history_id="100", token="lease"
    )
    worker.writer.release_history_sync = lambda *args, **kwargs: None
    worker.process(envelope("GMAIL_HISTORY_AVAILABLE", history_id="110"))
    assert runtime.calls[0][1] == "GOOGLE_INTEREST_HISTORY_SYNC"
    assert len(store.persisted) == 1


def test_busy_history_lease_retries_without_reporting_an_analysis_failure():
    worker, store, runtime, _ = processor()
    store.state["scan"].update(status="READY", completed_at="2026-09-09T00:00:00Z")

    def busy(*args, **kwargs):
        raise HistorySyncInProgress

    worker.writer.claim_history_sync = busy
    with pytest.raises(HistorySyncInProgress):
        worker.process(envelope("GMAIL_HISTORY_AVAILABLE", history_id="110"))
    assert not runtime.calls and not store.failures


def test_committed_page_queue_failure_does_not_change_success_to_analysis_error():
    worker, store, _, queue = processor()

    def reject(**args):
        raise RuntimeError("queue unavailable")

    queue.send = reject
    with pytest.raises(RuntimeError):
        worker.process(envelope())
    assert len(store.persisted) == 1
    assert not store.failures


def test_page_retry_replays_durable_continuation_without_another_model_call():
    worker, store, runtime, queue = processor()
    work = {
        "event_type": "MAIL_SCAN_CONTINUATION",
        "payload": {
            "request_id": IDENTITY,
            "profile_version": 1,
            "step": 1,
            "page_token": "next",
        },
    }
    store.item.update(scan_step={"N": "1"}, scan_next_json={"S": json.dumps(work)})
    worker.process(envelope())
    assert not runtime.calls
    assert queue.calls == [{"user_id": "owner", **work}]


class TransactionFailure(Exception):
    def __init__(self):
        self.response = {"CancellationReasons": [{"Code": "ConditionalCheckFailed"}]}


class Dynamo:
    exceptions = SimpleNamespace(TransactionCanceledException=TransactionFailure)

    def __init__(self):
        self.transactions = []

    def get_item(self, **request):
        return {}

    def transact_write_items(self, **request):
        self.transactions.append(request["TransactItems"])


class AuthDynamo(Dynamo):
    def __init__(self, *, reconnect=False):
        super().__init__()
        self.connection = {
            "status": {"S": "CONNECTED"},
            "mail_connection_id": {"S": "epoch"},
        }
        self.reconnect = reconnect

    def get_item(self, **request):
        if request["Key"]["SK"]["S"] == "CONNECTION#google":
            return {"Item": self.connection}
        return {
            "Item": {
                "profile_version": {"N": "1"},
                "scan_id": {"S": IDENTITY},
                "request_id": {"S": IDENTITY},
                **{
                    f"{kind}_json": {"S": json.dumps(value)}
                    for kind, value in state().items()
                },
            }
        }

    def transact_write_items(self, **request):
        if self.reconnect:
            self.connection["mail_connection_id"] = {"S": "new-epoch"}
            raise TransactionFailure
        super().transact_write_items(**request)


def test_auth_state_write_guards_connection_generation_and_preserves_profile():
    client = AuthDynamo()
    DynamoMailJobStore("table", client).authorization_required("owner", "epoch")
    connection, mail = [entry["Update"] for entry in client.transactions[0]]
    assert "mail_connection_id=:epoch" in connection["ConditionExpression"]
    assert "REMOVE GSI1PK,GSI1SK,GSI2PK,GSI2SK" in connection["UpdateExpression"]
    assert (
        connection["ExpressionAttributeValues"][":code"]["S"] == "GOOGLE_AUTH_REQUIRED"
    )
    assert "profile_json" not in mail["UpdateExpression"]
    assert "profile_version=:old_profile_version" in mail["ConditionExpression"]
    for key in (":recommendations", ":scan"):
        value = json.loads(mail["ExpressionAttributeValues"][key]["S"])
        assert (
            value["status"] == "ERROR" and value["error_code"] == "GOOGLE_AUTH_REQUIRED"
        )


def test_auth_state_cas_failure_after_reconnect_does_not_retry_new_connection():
    client = AuthDynamo(reconnect=True)
    DynamoMailJobStore("table", client).authorization_required("owner", "epoch")
    assert not client.transactions
    assert client.connection["mail_connection_id"]["S"] == "new-epoch"


def test_dynamo_publish_is_atomic_profile_and_connection_guarded_and_minimized():
    client = Dynamo()
    store = DynamoMailJobStore("table", client)
    result = snapshot()
    writer = DynamoConnectionWriter("table", client)
    store.persist(
        "owner", state(), {}, "epoch", result, result, writer, step=0, next_work=None
    )
    transaction = client.transactions[0]
    keys = [
        next(iter(operation.values()))["Key"]["SK"]["S"]
        for operation in transaction
        if "Put" not in operation
    ]
    assert f"EVIDENCE#{REF}" in keys
    assert f"EVIDENCE#{OTHER_REF}" not in keys
    assert not any(key.startswith("CANDIDATE#") for key in keys)
    serialized = json.dumps(transaction)
    assert "untrusted_text" not in serialized and "snippet" not in serialized
    mail_update = next(
        operation["Update"]
        for operation in transaction
        if operation.get("Update", {}).get("Key", {}).get("SK", {}).get("S")
        == "MAIL_INTERESTS#google"
    )
    assert "profile_version=:version" in mail_update["ConditionExpression"]
    assert "scan_id=:id" in mail_update["ConditionExpression"]
    assert "scan_step" in mail_update["ConditionExpression"]
    record = json.loads(mail_update["ExpressionAttributeValues"][":record"]["S"])
    assert (
        record["status"] == "READY"
        and record["matched_count"] == 1
        and record["processed_count"] == 2
    )
    assert (
        "mail_connection_id=:epoch" in transaction[-1]["Update"]["ConditionExpression"]
    )


@pytest.mark.parametrize("previous_warning", [True, False])
def test_partial_action_warning_survives_successful_followup_pages(previous_warning):
    client = Dynamo()
    saved = state()
    if previous_warning:
        saved["scan"]["error_code"] = "MAIL_ACTION_PREPARATION_INCOMPLETE"
    result = snapshot()
    page = _scan_page_details(result, interest_results=True)
    page["action_preparation_incomplete"] = not previous_warning
    DynamoMailJobStore("table", client).persist(
        "owner",
        saved,
        {},
        "epoch",
        result,
        page,
        DynamoConnectionWriter("table", client),
        step=0,
        next_work=None,
    )
    transaction = client.transactions[0]
    assert any("Put" in operation for operation in transaction)
    mail_update = transaction[-2]["Update"]
    scan = json.loads(mail_update["ExpressionAttributeValues"][":record"]["S"])
    assert scan["status"] == "READY" and scan["matched_count"] == 1
    assert scan["error_code"] == "MAIL_ACTION_PREPARATION_INCOMPLETE"
    assert transaction[-1]["Update"]["ExpressionAttributeValues"][":revision"] == {
        "N": "0"
    }


@pytest.mark.parametrize("same_scan", [True, False])
def test_action_warning_survives_transient_failure_but_does_not_leak_to_new_scan(
    same_scan,
):
    client = Dynamo()
    saved = state()
    saved["scan"].update(status="ERROR", error_code="MAIL_ANALYSIS_FAILED")
    item = {"scan_action_warning_id": {"S": IDENTITY if same_scan else "b" * 32}}
    result = snapshot()
    page = _scan_page_details(result, interest_results=True)
    DynamoMailJobStore("table", client).persist(
        "owner",
        saved,
        item,
        "epoch",
        result,
        page,
        DynamoConnectionWriter("table", client),
        step=0,
        next_work=None,
    )
    update = client.transactions[0][-2]["Update"]
    scan = json.loads(update["ExpressionAttributeValues"][":record"]["S"])
    assert scan["status"] == "READY"
    assert scan["error_code"] == (
        "MAIL_ACTION_PREPARATION_INCOMPLETE" if same_scan else None
    )
    assert ("scan_action_warning_id=:id" in update["UpdateExpression"]) == same_scan


def test_dynamo_recommendation_completion_updates_no_profile_fields():
    client = Dynamo()
    DynamoMailJobStore("table", client).finish_recommendations(
        "owner",
        IDENTITY,
        "epoch",
        {
            "status": "INTEREST_TAGS",
            "tags": [],
            "title_count": 0,
            "generated_at": "2026-09-09T00:00:00Z",
        },
    )
    update = client.transactions[0][0]["Update"]
    assert update["UpdateExpression"] == "SET recommendations_json=:record"
    assert "request_id=:id" in update["ConditionExpression"]
    assert "profile_version" not in json.dumps(update)


def test_final_scan_page_does_not_skip_the_catchup_history_range():
    client = Dynamo()
    result = snapshot()
    result.pop("history_id")
    result["status"] = "SCAN_PAGE"
    DynamoMailJobStore("table", client).persist(
        "owner",
        state(),
        {},
        "epoch",
        result,
        result,
        DynamoConnectionWriter("table", client),
        step=1,
        next_work={
            "event_type": "GMAIL_HISTORY_AVAILABLE",
            "payload": {"history_id": "110"},
        },
    )
    connection_update = client.transactions[0][-1]["Update"]
    assert "gmail_history_id" not in connection_update["UpdateExpression"]


def test_dynamo_conditional_conflict_is_stale_work_not_a_successful_empty_scan():
    client = Dynamo()

    def reject(**request):
        raise TransactionFailure

    client.transact_write_items = reject
    with pytest.raises(StaleMailJob):
        store = DynamoMailJobStore("table", client)
        store.transact([store.guard("owner", "epoch")])


def test_unmatched_evidence_cannot_become_candidate():
    client = Dynamo()
    result = snapshot()
    result["candidates"] = [{"evidence_refs": [OTHER_REF]}]
    with pytest.raises(ValueError, match="unmatched"):
        DynamoMailJobStore("table", client).persist(
            "owner",
            state(),
            {},
            "epoch",
            result,
            result,
            DynamoConnectionWriter("table", client),
            step=0,
            next_work=None,
        )
    assert not client.transactions


@pytest.mark.parametrize("importance", ["HIGH", "NORMAL", "LOW", "ABSENT"])
def test_mail_importance_is_persisted_without_changing_result_identity(importance):
    client = Dynamo()
    result = snapshot()
    match = result["interest_matches"][0]
    if importance != "ABSENT":
        match["importance"] = importance
    if importance == "HIGH":
        match["matched_tags"] = []
    DynamoMailJobStore("table", client).persist(
        "owner",
        state(),
        {},
        "epoch",
        result,
        result,
        DynamoConnectionWriter("table", client),
        step=0,
        next_work=None,
    )
    saved = next(
        operation["Put"]["Item"]
        for operation in client.transactions[0]
        if "Put" in operation
        and operation["Put"]["Item"]["SK"]["S"].startswith("MAIL_RESULT#")
    )
    assert (
        saved["SK"]["S"]
        == f"MAIL_RESULT#{IDENTITY}#{hashlib.sha256(REF.encode()).hexdigest()}"
    )
    assert json.loads(saved["result_json"]["S"]) == match
    if importance == "ABSENT":
        assert "importance" not in json.loads(saved["result_json"]["S"])


@pytest.mark.parametrize("importance", [None, "URGENT", "normal", 1, []])
def test_invalid_mail_importance_rejects_the_transaction(importance):
    client = Dynamo()
    result = snapshot()
    result["interest_matches"][0]["importance"] = importance
    with pytest.raises(ValueError, match="importance"):
        DynamoMailJobStore("table", client).persist(
            "owner",
            state(),
            {},
            "epoch",
            result,
            result,
            DynamoConnectionWriter("table", client),
            step=0,
            next_work=None,
        )
    assert not client.transactions
