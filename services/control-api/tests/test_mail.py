import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from quietpilot_control_api.handlers import handle_request
from quietpilot_control_api.mail import (
    DynamoMailStore,
    MailConflict,
    MailInputError,
    MailInterestService,
    empty_state,
    profile_input,
)


class ConditionalFailure(Exception):
    def __init__(self):
        self.response = {"CancellationReasons": [{"Code": "ConditionalCheckFailed"}]}


class Client:
    exceptions = SimpleNamespace(
        TransactionCanceledException=ConditionalFailure,
        ConditionalCheckFailedException=ConditionalFailure,
    )

    def __init__(self, *, status="CONNECTED", epoch="connection-one"):
        self.state = empty_state()
        self.state["profile"].update(tags=["학교"], version=1)
        self.connection = {"status": {"S": status}, "mail_connection_id": {"S": epoch}}
        self.extra = {
            "scan_connection_id": {"S": epoch},
            "recommendations_connection_id": {"S": epoch},
        }
        self.transactions = []
        self.queries = []
        self.query_response = {"Items": []}

    def get_item(self, **request):
        if request["Key"]["SK"]["S"] == "CONNECTION#google":
            return {"Item": self.connection}
        return {
            "Item": {
                **self.extra,
                **{
                    f"{name}_json": {"S": json.dumps(value)}
                    for name, value in self.state.items()
                },
            }
        }

    def transact_write_items(self, **request):
        self.transactions.append(request["TransactItems"])

    def query(self, **request):
        self.queries.append(request)
        return self.query_response


@pytest.mark.parametrize(
    "tags", [["#학교"], ["학교 공지"], ["C#"], ["＃학교"], [""], ["a" * 33], ["x"] * 9]
)
def test_profile_rejects_invalid_internal_tags(tags):
    with pytest.raises(MailInputError):
        profile_input(tags, "", 0)


def test_profile_normalizes_tags_and_preserves_description():
    assert profile_input([" ＡＩ ", "ai", "학교", "C++"], "  장학금 제외  ", 0) == (
        ["AI", "학교", "C++"],
        "  장학금 제외  ",
        0,
    )


def test_profile_edit_atomically_invalidates_results_and_guards_connected_generation():
    client = Client()
    store = DynamoMailStore("table", client)
    store.save_profile("owner", ["학교"], "", 1)
    transaction = client.transactions[0]
    update = transaction[0]["Update"]
    assert update["Key"]["PK"]["S"] == "USER#owner"
    assert update["ConditionExpression"] == "profile_version=:expected"
    assert update["ExpressionAttributeValues"][":expected"] == {"N": "1"}
    profile = json.loads(update["ExpressionAttributeValues"][":profile"]["S"])
    scan = json.loads(update["ExpressionAttributeValues"][":scan"]["S"])
    assert profile["version"] == scan["profile_version"] == 2
    assert scan["status"] == "PENDING" and len(scan["scan_id"]) == 32
    assert "scan_step=:zero" in update["UpdateExpression"]
    assert (
        "mail_connection_id=:epoch"
        in transaction[1]["ConditionCheck"]["ConditionExpression"]
    )


@pytest.mark.parametrize(
    "tags,description,status", [([], "", "CONNECTED"), (["학교"], "", "DISCONNECTED")]
)
def test_blank_or_disconnected_profile_does_not_schedule_a_scan(
    tags, description, status
):
    client = Client(status=status)
    DynamoMailStore("table", client).save_profile("owner", tags, description, 1)
    transaction = client.transactions[0]
    scan = json.loads(
        transaction[0]["Update"]["ExpressionAttributeValues"][":scan"]["S"]
    )
    assert scan["status"] == "NOT_STARTED" and scan["scan_id"] is None


def test_profile_conflict_is_reported_without_overwriting():
    client = Client()

    def reject(**request):
        raise ConditionalFailure

    client.transact_write_items = reject
    with pytest.raises(MailConflict):
        DynamoMailStore("table", client).save_profile("owner", ["학교"], "", 1)


@pytest.mark.parametrize(
    "kind,method,event,field",
    [
        ("scan", "scan", "MAIL_SCAN_REQUESTED", "scan_id"),
        (
            "recommendations",
            "recommend",
            "MAIL_RECOMMENDATIONS_REQUESTED",
            "request_id",
        ),
    ],
)
def test_explicit_retry_dispatches_pending_work_with_its_persisted_identity(
    kind, method, event, field
):
    client = Client()
    client.state[kind].update(status="PENDING", **{field: "a" * 32})
    client.extra[field] = {"S": "a" * 32}
    if kind == "scan":
        client.state[kind]["profile_version"] = 1
    else:
        client.state["profile"]["version"] = 8
    queued = []
    service = MailInterestService(
        DynamoMailStore("table", client),
        SimpleNamespace(send=lambda **values: queued.append(values)),
    )

    # The previous request committed PENDING but stopped before the queue send.
    assert service.state("owner")[kind]["status"] == "PENDING"
    assert not queued
    current = getattr(service, method)("owner")

    assert current[kind][field] == "a" * 32
    assert queued == [
        {
            "user_id": "owner",
            "event_type": event,
            "payload": {
                "request_id": "a" * 32,
                "profile_version": client.state["profile"]["version"],
            },
        }
    ]
    assert not client.transactions


def test_scan_retry_recovers_profile_save_interrupted_before_dispatch():
    client = Client()
    store = DynamoMailStore("table", client)
    store.save_profile("owner", ["장학금"], "교내 소식", 1)
    update = client.transactions[0][0]["Update"]["ExpressionAttributeValues"]
    # Materialize the accepted transaction, then retry without the original send.
    client.state["profile"] = json.loads(update[":profile"]["S"])
    client.state["scan"] = json.loads(update[":scan"]["S"])
    client.extra.update(
        scan_id=update[":id"],
        scan_connection_id=update[":epoch"],
        profile_version=update[":next"],
    )
    queued = []
    service = MailInterestService(
        store, SimpleNamespace(send=lambda **values: queued.append(values))
    )

    current = service.scan("owner")

    assert current["profile"]["version"] == 2
    assert current["scan"]["scan_id"] == update[":id"]["S"]
    assert queued[0]["payload"] == {
        "request_id": update[":id"]["S"],
        "profile_version": 2,
    }
    assert len(client.transactions) == 1


@pytest.mark.parametrize("kind", ["scan", "recommendations"])
def test_legacy_scan_state_accepts_recovery_jobs_with_readonly_scope(kind):
    client = Client(status="SCANNING")
    client.connection["granted_scopes"] = {
        "L": [{"S": "https://www.googleapis.com/auth/gmail.readonly"}]
    }
    _, created = DynamoMailStore("table", client).request("owner", kind)
    assert created
    assert client.transactions[0][1]["ConditionCheck"]["ExpressionAttributeValues"][
        ":connected"
    ] == {"S": "SCANNING"}


def test_reconnect_hides_previous_mail_generation():
    client = Client(epoch="new-account")
    client.extra["scan_connection_id"] = {"S": "old-account"}
    client.state["scan"].update(status="READY", scan_id="a" * 32, profile_version=1)
    store = DynamoMailStore("table", client)
    assert store.public_state("owner")["scan"]["status"] == "NOT_STARTED"
    assert store.results("owner", None)["items"] == []
    assert not client.queries


def test_public_state_cannot_authorize_old_content_with_new_item_metadata():
    client = Client(epoch="old-account")
    client.state["recommendations"].update(
        status="READY", request_id="a" * 32, tags=[{"tag": "이전계정"}]
    )
    client.state["scan"].update(status="READY", scan_id="a" * 32, profile_version=1)
    get_item = client.get_item
    mail_reads = 0

    def reconnect_after_read(**request):
        nonlocal mail_reads
        response = get_item(**request)
        if request["Key"]["SK"]["S"] == "MAIL_INTERESTS#google":
            mail_reads += 1
            if mail_reads == 1:
                client.connection["mail_connection_id"] = {"S": "new-account"}
                for kind in ("scan", "recommendations"):
                    client.extra[f"{kind}_connection_id"] = {"S": "new-account"}
                    field = "scan_id" if kind == "scan" else "request_id"
                    client.extra[field] = {"S": "b" * 32}
                    client.state[kind].update(status="PENDING", **{field: "b" * 32})
                client.state["recommendations"]["tags"] = []
        return response

    client.get_item = reconnect_after_read
    current = DynamoMailStore("table", client).public_state("owner")

    assert current["scan"]["status"] == "NOT_STARTED"
    assert current["recommendations"]["status"] == "NOT_STARTED"
    assert current["recommendations"]["tags"] == []
    assert mail_reads == 1


@pytest.mark.parametrize(
    "kind,field", [("scan", "scan_id"), ("recommendations", "request_id")]
)
def test_request_reconnect_interleaving_dispatches_only_the_current_saved_job(
    kind, field
):
    client = Client(epoch="old-account")
    client.state[kind].update(status="PENDING", **{field: "a" * 32})
    client.state["scan"]["profile_version"] = 1
    client.extra[field] = {"S": "a" * 32}
    get_item = client.get_item
    changed = False

    def reconnect_after_read(**request):
        nonlocal changed
        response = get_item(**request)
        if request["Key"]["SK"]["S"] == "MAIL_INTERESTS#google" and not changed:
            changed = True
            client.connection["mail_connection_id"] = {"S": "new-account"}
            client.extra[f"{kind}_connection_id"] = {"S": "new-account"}
            client.extra[field] = {"S": "b" * 32}
            client.state[kind][field] = "b" * 32
            client.state["profile"]["version"] = 2
            client.state["scan"]["profile_version"] = 2
        return response

    def reject_old_identity(**request):
        assert request["TransactItems"][0]["Update"]["ExpressionAttributeValues"][
            ":old"
        ] == {"S": "a" * 32}
        raise ConditionalFailure

    client.get_item = reconnect_after_read
    client.transact_write_items = reject_old_identity
    current, dispatch = DynamoMailStore("table", client).request("owner", kind)

    assert dispatch
    assert current[kind][field] == "b" * 32
    assert current["profile"]["version"] == 2


@pytest.mark.parametrize("kind", ["scan", "recommendations"])
def test_request_conflict_during_oauth_setup_never_dispatches_projected_pending(kind):
    client = Client()

    def begin_setup(**request):
        client.connection.update(
            status={"S": "CONNECTING"},
            mail_connection_id={"S": "b" * 32},
            granted_scopes={
                "L": [{"S": "https://www.googleapis.com/auth/gmail.readonly"}]
            },
        )
        raise ConditionalFailure

    client.transact_write_items = begin_setup
    queued = []
    service = MailInterestService(
        DynamoMailStore("table", client),
        SimpleNamespace(send=lambda **values: queued.append(values)),
    )
    current = (service.scan if kind == "scan" else service.recommend)("owner")

    assert current[kind]["status"] == "PENDING"
    assert not queued


def test_cursor_cannot_switch_owner_or_profile_generation():
    client = Client()
    client.state["scan"].update(status="READY", scan_id="a" * 32, profile_version=1)
    cursor = base64.urlsafe_b64encode(
        json.dumps(
            {
                "owner": "different-owner",
                "scan_id": "a" * 32,
                "version": 1,
                "last": "MAIL_RESULT#" + "a" * 32 + "#x",
            }
        ).encode()
    ).decode()
    with pytest.raises(MailConflict):
        DynamoMailStore("table", client).results("owner", cursor)
    assert not client.queries


def test_api_uses_jwt_owner_for_interest_save_and_maps_conflict():
    class Service:
        def save(self, owner, **values):
            assert owner == "trusted-owner"
            assert values == {
                "tags": ["학교"],
                "description": "",
                "expected_version": 2,
            }
            raise MailConflict

    event = {
        "requestContext": {
            "requestId": "r",
            "routeKey": "PUT /v1/mail/interests",
            "http": {"method": "PUT", "path": "/v1/mail/interests"},
            "authorizer": {"jwt": {"claims": {"sub": "trusted-owner"}}},
        },
        "body": json.dumps(
            {"tags": ["학교"], "description": "", "expected_version": 2}
        ),
    }
    response = handle_request(event, mail_service=Service())
    assert response["statusCode"] == 409
    assert json.loads(response["body"])["error"] == "MAIL_VERSION_CONFLICT"


def test_queue_failure_marks_only_accepted_job_as_error():
    state = empty_state()
    state["scan"].update(status="PENDING", scan_id="a" * 32, profile_version=1)
    state["profile"]["version"] = 1
    failed = []
    work = SimpleNamespace(
        event_type="MAIL_SCAN_REQUESTED",
        payload={"request_id": "a" * 32, "profile_version": 1},
    )
    store = SimpleNamespace(
        save_profile=lambda *args: (state, True),
        scan_work=lambda *args: work,
        fail_request=lambda *args: failed.append(args),
    )

    def reject(**values):
        raise RuntimeError("queue unavailable")

    with pytest.raises(RuntimeError):
        MailInterestService(store, SimpleNamespace(send=reject)).save(
            "owner", tags=["학교"], description="", expected_version=0
        )
    assert failed == [("owner", "scan", "a" * 32, "MAIL_QUEUE_UNAVAILABLE", work)]


def test_authorized_connecting_setup_is_pending_and_recommendation_post_coalesces():
    client = Client(status="CONNECTING", epoch="a" * 32)
    client.connection["granted_scopes"] = {
        "L": [{"S": "https://www.googleapis.com/auth/gmail.readonly"}]
    }
    client.state = empty_state()
    store = DynamoMailStore("table", client)
    queued = []
    service = MailInterestService(
        store, SimpleNamespace(send=lambda **values: queued.append(values))
    )
    current = service.state("owner")
    assert current["recommendations"]["status"] == "PENDING"
    assert current["recommendations"]["request_id"] == "a" * 32
    assert current["scan"]["status"] == "NOT_STARTED"
    assert service.recommend("owner") == current
    assert not queued and not client.transactions


def test_consent_required_connecting_is_not_a_queued_mail_setup():
    client = Client(status="CONNECTING", epoch="a" * 32)
    client.connection["granted_scopes"] = {"L": []}
    client.state = empty_state()
    store = DynamoMailStore("table", client)
    assert store.public_state("owner")["recommendations"]["status"] == "NOT_STARTED"
    with pytest.raises(MailInputError):
        store.request("owner", "recommendations")
    assert not client.transactions


def test_manual_profile_during_authorized_setup_waits_for_worker_dispatch():
    client = Client(status="CONNECTING", epoch="a" * 32)
    client.connection["granted_scopes"] = {
        "L": [{"S": "https://www.googleapis.com/auth/gmail.readonly"}]
    }
    store = DynamoMailStore("table", client)
    _, dispatch = store.save_profile("owner", ["장학금"], "  교내 소식  ", 1)
    assert not dispatch
    update = client.transactions[0][0]["Update"]
    scan = json.loads(update["ExpressionAttributeValues"][":scan"]["S"])
    profile = json.loads(update["ExpressionAttributeValues"][":profile"]["S"])
    assert scan["status"] == "PENDING" and scan["profile_version"] == 2
    assert len(scan["scan_id"]) == 32
    assert profile["tags"] == ["장학금"] and profile["description"] == "  교내 소식  "
    assert update["ExpressionAttributeValues"][":epoch"] == {"S": "a" * 32}
    assert client.transactions[0][1]["ConditionCheck"]["ExpressionAttributeValues"][
        ":connected"
    ] == {"S": "CONNECTING"}


def test_setup_failure_surfaces_error_instead_of_perpetual_pending():
    client = Client(status="ERROR", epoch="a" * 32)
    client.connection.update(
        granted_scopes={"L": [{"S": "https://www.googleapis.com/auth/gmail.readonly"}]},
        error_code={"S": "MAIL_SETUP_FAILED"},
    )
    client.state["scan"].update(status="PENDING", scan_id="b" * 32, profile_version=1)
    current = DynamoMailStore("table", client).public_state("owner")
    assert current["recommendations"]["status"] == current["scan"]["status"] == "ERROR"
    assert (
        current["recommendations"]["error_code"]
        == current["scan"]["error_code"]
        == "MAIL_SETUP_FAILED"
    )
    assert current["profile"]["tags"] == ["학교"]


def test_auth_error_remains_visible_and_retry_requests_do_not_enqueue_work():
    client = Client(status="ERROR")
    client.connection.update(
        error_code={"S": "GOOGLE_AUTH_REQUIRED"},
        granted_scopes={"L": [{"S": "https://www.googleapis.com/auth/gmail.readonly"}]},
    )
    client.state["recommendations"].update(
        status="ERROR", request_id="a" * 32, error_code="GOOGLE_AUTH_REQUIRED"
    )
    client.state["scan"].update(
        status="ERROR",
        scan_id="b" * 32,
        profile_version=1,
        error_code="GOOGLE_AUTH_REQUIRED",
    )
    queue = []
    store = DynamoMailStore("table", client)
    service = MailInterestService(
        store, SimpleNamespace(send=lambda **values: queue.append(values))
    )
    current = service.state("owner")
    assert (
        current["recommendations"]["error_code"]
        == current["scan"]["error_code"]
        == "GOOGLE_AUTH_REQUIRED"
    )
    assert service.recommend("owner") == service.scan("owner") == current
    assert current["profile"]["tags"] == ["학교"]
    assert not queue and not client.transactions
    _, dispatch = store.save_profile("owner", ["장학금"], "새 관심사", 1)
    assert not dispatch
    saved = json.loads(
        client.transactions[0][0]["Update"]["ExpressionAttributeValues"][":profile"][
            "S"
        ]
    )
    assert saved["tags"] == ["장학금"] and saved["version"] == 2


def mail_result(reference, *, importance="NORMAL", received_at=None):
    result = {
        "evidence_ref": reference,
        "title": "메일 제목",
        "summary": "메일 요약이에요.",
        "reason": "확인할 내용이에요.",
        "sender_domain": "example.test",
        "received_at": received_at,
        "matched_tags": [],
    }
    if importance != "ABSENT":
        result["importance"] = importance
    return result


class ResultsClient(Client):
    def __init__(self, results, *, storage_page_size=20):
        super().__init__()
        self.state["scan"].update(status="READY", scan_id="a" * 32, profile_version=1)
        self.extra.update(scan_step={"N": "1"}, scan_id={"S": "a" * 32})
        self.storage_page_size = storage_page_size
        self.records = [
            {
                "PK": {"S": "USER#owner"},
                "SK": {
                    "S": f"MAIL_RESULT#{'a' * 32}#{hashlib.sha256(item['evidence_ref'].encode()).hexdigest()}"
                },
                "result_json": {"S": json.dumps(item)},
            }
            for item in results
        ]

    def query(self, **request):
        self.queries.append(dict(request))
        pk = request["ExpressionAttributeValues"][":pk"]
        prefix = request["ExpressionAttributeValues"][":prefix"]["S"]
        start = request.get("ExclusiveStartKey", {}).get("SK", {}).get("S", "")
        records = sorted(
            [
                item
                for item in self.records
                if item["PK"] == pk
                and item["SK"]["S"].startswith(prefix)
                and item["SK"]["S"] > start
            ],
            key=lambda item: item["SK"]["S"],
        )
        page = records[: min(request["Limit"], self.storage_page_size)]
        response = {"Items": page}
        if len(page) < len(records):
            response["LastEvaluatedKey"] = {key: page[-1][key] for key in ("PK", "SK")}
        return response


def test_importance_sort_reads_beyond_first_storage_page_and_paginates_completely():
    values = [mail_result(f"ref-{index:03d}") for index in range(75)]
    client = ResultsClient(values)
    last = max(client.records, key=lambda item: item["SK"]["S"])
    high = json.loads(last["result_json"]["S"])
    high["importance"] = "HIGH"
    last["result_json"]["S"] = json.dumps(high)
    foreign = mail_result("other-owner", importance="HIGH")
    client.records.append(
        {
            "PK": {"S": "USER#other"},
            "SK": {"S": f"MAIL_RESULT#{'a' * 32}#0"},
            "result_json": {"S": json.dumps(foreign)},
        }
    )
    store = DynamoMailStore("table", client)
    page = store.results("owner", None)
    assert page["items"][0] == high
    assert len(client.queries) == 4
    received = page["items"][:]
    while page["next_cursor"]:
        page = store.results("owner", page["next_cursor"])
        received.extend(page["items"])
    assert len(received) == 75
    assert [item["evidence_ref"] for item in received] == [
        high["evidence_ref"]
    ] + sorted(
        item["evidence_ref"]
        for item in values
        if item["evidence_ref"] != high["evidence_ref"]
    )
    assert all(
        request["ExpressionAttributeValues"][":pk"] == {"S": "USER#owner"}
        for request in client.queries
    )


def test_importance_then_receipt_time_and_reference_order_treats_legacy_as_normal():
    values = [
        mail_result("low-new", importance="LOW", received_at="2026-09-13T00:00:00Z"),
        mail_result("normal-old", received_at="2026-09-10T00:00:00Z"),
        mail_result("normal-z", received_at="2026-09-12T00:00:00Z"),
        mail_result(
            "normal-a", importance="ABSENT", received_at="2026-09-12T09:00:00+09:00"
        ),
        mail_result("normal-unknown"),
        mail_result("high-unknown", importance="HIGH"),
    ]
    page = DynamoMailStore("table", ResultsClient(values)).results("owner", None)
    assert [item["evidence_ref"] for item in page["items"]] == [
        "high-unknown",
        "normal-a",
        "normal-z",
        "normal-old",
        "normal-unknown",
        "low-new",
    ]
    assert "importance" not in page["items"][1]


@pytest.mark.parametrize("importance", [None, "URGENT", "normal", 1, []])
def test_invalid_stored_importance_is_rejected(importance):
    store = DynamoMailStore(
        "table", ResultsClient([mail_result("ref", importance=importance)])
    )
    with pytest.raises(ValueError, match="importance"):
        store.results("owner", None)


def test_sorted_cursor_is_bound_to_owner_generation_and_complete_result_set():
    client = ResultsClient([mail_result(f"ref-{index}") for index in range(35)])
    store = DynamoMailStore("table", client)
    cursor = store.results("owner", None)["next_cursor"]
    count = len(client.queries)
    with pytest.raises(MailConflict):
        store.results("other", cursor)
    assert len(client.queries) == count
    item = json.loads(client.records[0]["result_json"]["S"])
    item["importance"] = "HIGH"
    client.records[0]["result_json"]["S"] = json.dumps(item)
    with pytest.raises(MailConflict, match="changed during pagination"):
        store.results("owner", cursor)


@pytest.mark.parametrize("generation", ["profile", "scan", "connection"])
def test_sorted_cursor_cannot_cross_profile_scan_or_connection_generation(generation):
    client = ResultsClient([mail_result(f"ref-{index}") for index in range(35)])
    store = DynamoMailStore("table", client)
    cursor = store.results("owner", None)["next_cursor"]
    if generation == "profile":
        client.state["profile"]["version"] = 2
    elif generation == "scan":
        client.state["scan"]["scan_id"] = "b" * 32
    else:
        client.connection["mail_connection_id"] = {"S": "new-connection"}
        client.extra["scan_connection_id"] = {"S": "new-connection"}
    count = len(client.queries)
    with pytest.raises(MailConflict, match="generation changed"):
        store.results("owner", cursor)
    assert len(client.queries) == count


def test_legacy_hash_cursor_is_rejected_before_reading_results():
    client = ResultsClient([])
    cursor = base64.urlsafe_b64encode(
        json.dumps(
            {
                "owner": hashlib.sha256(b"owner").hexdigest(),
                "scan_id": "a" * 32,
                "version": 1,
                "last": f"MAIL_RESULT#{'a' * 32}#old",
            }
        ).encode()
    ).decode()
    with pytest.raises(MailConflict, match="ordering changed"):
        DynamoMailStore("table", client).results("owner", cursor)
    assert not client.queries


@pytest.mark.parametrize("count", [512, 513])
def test_result_ordering_bound_is_complete_or_fails_without_truncation(count):
    client = ResultsClient(
        [mail_result(f"ref-{index}") for index in range(count)], storage_page_size=100
    )
    store = DynamoMailStore("table", client)
    if count == 513:
        with pytest.raises(MailConflict, match="ordering bound"):
            store.results("owner", None)
    else:
        assert len(store.results("owner", None)["items"]) == 30
    assert client.queries[-1]["Limit"] == 13
    assert len(client.queries) == 6


def test_inflight_history_write_invalidates_multi_query_result_snapshot():
    client = ResultsClient([mail_result(f"ref-{index}") for index in range(35)])
    query = client.query

    def change_step(**request):
        response = query(**request)
        client.extra["scan_step"] = {"N": "2"}
        return response

    client.query = change_step
    with pytest.raises(MailConflict, match="generation changed"):
        DynamoMailStore("table", client).results("owner", None)
