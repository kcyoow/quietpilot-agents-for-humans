from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import boto3
import pytest
from quietpilot_control_api.notifications import PushTokenService
from quietpilot_worker.notifications import (
    EXPO_SEND_URL,
    ExpoPushTransport,
    NotificationDispatcher,
    PushOutcome,
    notification_put,
)

moto = pytest.importorskip("moto")
TABLE = "notifications"
NOW = 2_000_000_000
TOKEN = "ExpoPushToken[synthetic_token_000001]"
DEVICE = "device-0001"


class Fixture:
    def __init__(self, db):
        self.db = db
        self.now = NOW
        self.sent = []
        self.registry = PushTokenService(TABLE, db, clock=lambda: self.now)
        self.dispatcher = NotificationDispatcher(
            TABLE, db, clock=lambda: self.now, transport=self.send
        )

    def send(self, message):
        self.sent.append(message)
        return PushOutcome("ACCEPTED", "EXPO_TICKET_ACCEPTED")

    def register(self, owner="owner", device=DEVICE, token=TOKEN):
        return self.registry.register(
            owner,
            device_id=device,
            expo_push_token=token,
            platform="android",
            app_version="0.1.0",
        )

    def case(
        self, owner="owner", case="case-one", version=2, status="DECISION_REQUIRED"
    ):
        self.db.put_item(
            TableName=TABLE,
            Item={
                "PK": {"S": f"CASE#{case}"},
                "SK": {"S": "META"},
                "entity_type": {"S": "case"},
                "case_id": {"S": case},
                "user_id": {"S": owner},
                "version": {"N": str(version)},
                "status": {"S": status},
                "title": {"S": "PRIVATE_SOURCE_TITLE"},
                "parameters": {"S": "PRIVATE_SOURCE_PARAMETERS"},
            },
        )

    def event(
        self, owner="owner", case="case-one", version=2, kind="DECISION_REQUIRED"
    ):
        put = notification_put(TABLE, owner, case, version, kind, self.now)
        self.db.transact_write_items(TransactItems=[put])
        return put["Put"]["Item"]

    def read(self, event):
        return self.db.get_item(
            TableName=TABLE,
            Key={"PK": event["PK"], "SK": event["SK"]},
            ConsistentRead=True,
        )["Item"]


@pytest.fixture
def fixture():
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
        db.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": n, "AttributeType": "S"}
                for n in ("PK", "SK", "GSI1PK", "GSI1SK")
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield Fixture(db)


def test_atomic_producer_deduplication_cannot_repeat_case_transition(fixture):
    fixture.case(version=1)
    event = notification_put(TABLE, "owner", "case-one", 2, "DECISION_REQUIRED", NOW)
    transition = {
        "Update": {
            "TableName": TABLE,
            "Key": {"PK": {"S": "CASE#case-one"}, "SK": {"S": "META"}},
            "UpdateExpression": "SET #v=:new",
            "ConditionExpression": "#v=:old",
            "ExpressionAttributeNames": {"#v": "version"},
            "ExpressionAttributeValues": {":old": {"N": "1"}, ":new": {"N": "2"}},
        }
    }
    fixture.db.transact_write_items(TransactItems=[transition, event])
    with pytest.raises(fixture.db.exceptions.TransactionCanceledException):
        fixture.db.transact_write_items(TransactItems=[transition, event])
    assert fixture.read(event["Put"]["Item"])["status"] == {"S": "PENDING"}


def test_event_identity_binds_owner_case_version_kind_but_not_time():
    base = notification_put(TABLE, "owner", "case-one", 2, "DECISION_REQUIRED", NOW)
    changed_time = notification_put(
        TABLE, "owner", "case-one", 2, "DECISION_REQUIRED", NOW + 1
    )
    assert base["Put"]["Item"]["event_id"] == changed_time["Put"]["Item"]["event_id"]
    for args in [
        ("other", "case-one", 2, "DECISION_REQUIRED"),
        ("owner", "case-two", 2, "DECISION_REQUIRED"),
        ("owner", "case-one", 3, "DECISION_REQUIRED"),
        ("owner", "case-one", 2, "PLAN_CHANGED"),
    ]:
        assert (
            notification_put(TABLE, *args, NOW)["Put"]["Item"]["event_id"]
            != base["Put"]["Item"]["event_id"]
        )


@pytest.mark.parametrize(
    "kind", ["CANDIDATE", "COMPLETED", "SUCCESS", "MAIL_RESULT", None]
)
def test_ordinary_candidates_success_and_unrecognized_kinds_cannot_enqueue(kind):
    with pytest.raises(ValueError):
        notification_put(TABLE, "owner", "case-one", 2, kind, NOW)


@pytest.mark.parametrize("version", [0, -1, True, "2", 2_147_483_648])
def test_producer_requires_exact_positive_case_version(version):
    with pytest.raises(ValueError):
        notification_put(TABLE, "owner", "case-one", version, "DECISION_REQUIRED", NOW)


def test_ticket_acceptance_is_persisted_once_without_source_or_delivery_claim(
    fixture, caplog, capsys
):
    fixture.register()
    fixture.case()
    event = fixture.event()
    assert fixture.dispatcher.flush("owner")["accepted"] == 1
    assert len(fixture.sent) == 1
    message = fixture.sent[0]
    assert message["data"] == {
        "case_id": "case-one",
        "event_id": event["event_id"]["S"],
        "kind": "DECISION_REQUIRED",
    }
    assert set(message) == {"to", "title", "body", "data", "ttl", "channelId"}
    assert message["channelId"] == "default"
    assert "PRIVATE_SOURCE" not in json.dumps(message)
    result = fixture.read(event)
    assert result["status"] == {"S": "ACCEPTED"}
    assert "delivered" not in result and "verified" not in result
    assert "GSI1PK" not in result
    fixture.dispatcher.flush("owner")
    assert len(fixture.sent) == 1
    assert caplog.text == "" and capsys.readouterr().out == ""


def test_owner_isolation_and_missing_owned_case_suppress(fixture):
    fixture.register()
    fixture.case(owner="other")
    event = fixture.event()
    assert fixture.dispatcher.flush("other")["accepted"] == 0
    assert fixture.dispatcher.flush("owner")["suppressed"] == 1
    assert fixture.read(event)["status"] == {"S": "SUPPRESSED"}
    assert fixture.sent == []


@pytest.mark.parametrize("exact", [False, True])
def test_pipe_owner_register_outbox_dispatch_preserves_exact_subject_and_isolation(
    fixture, exact
):
    owner = "tenant|non-uuid-subject"
    other = "tenant|different-subject"
    other_token = "ExpoPushToken[synthetic_other_owner_token]"
    fixture.register(owner)
    fixture.case(owner=owner)
    event = fixture.event(owner=owner)
    fixture.register(other, token=other_token)
    fixture.case(owner=other, case="case-other")
    other_event = fixture.event(owner=other, case="case-other")
    assert event["user_id"] == {"S": owner}
    assert event["PK"] == {"S": f"USER#{owner}"}
    assert (
        fixture.dispatcher.flush(other, event_id=event["event_id"]["S"])["accepted"]
        == 0
    )
    assert fixture.sent == []
    arguments = {"event_id": event["event_id"]["S"]} if exact else {}
    assert fixture.dispatcher.flush(owner, **arguments)["accepted"] == 1
    assert [message["to"] for message in fixture.sent] == [TOKEN]
    assert fixture.read(other_event)["status"] == {"S": "PENDING"}
    arguments = {"event_id": other_event["event_id"]["S"]} if exact else {}
    assert fixture.dispatcher.flush(other, **arguments)["accepted"] == 1
    assert [message["to"] for message in fixture.sent] == [TOKEN, other_token]


def test_relaxed_owner_does_not_relax_case_device_or_event_identifiers(fixture):
    owner = "tenant|non-uuid-subject"
    with pytest.raises(ValueError):
        notification_put(TABLE, owner, "case|one", 2, "DECISION_REQUIRED", NOW)
    with pytest.raises(ValueError):
        fixture.register(owner, device="device|one")
    with pytest.raises(ValueError):
        fixture.dispatcher.flush(owner, event_id="event|one")


@pytest.mark.parametrize("owner", [None, "", " \t", "a" * 257])
def test_worker_owner_still_rejects_invalid_authoritative_subject(owner, fixture):
    with pytest.raises(ValueError):
        notification_put(TABLE, owner, "case-one", 2, "DECISION_REQUIRED", NOW)
    with pytest.raises(ValueError):
        fixture.dispatcher.flush(owner)


def test_identical_app_resume_does_not_invalidate_in_flight_device_generation(fixture):
    fixture.register()
    fixture.case()
    fixture.event()
    real_permit = fixture.dispatcher._permit

    def resume_then_permit(event, device):
        fixture.register()
        return real_permit(event, device)

    fixture.dispatcher._permit = resume_then_permit
    assert fixture.dispatcher.flush("owner")["accepted"] == 1
    assert len(fixture.sent) == 1


@pytest.mark.parametrize(
    "status",
    ["COMPLETED", "STOPPED", "PREPARING", "QUEUED", "RUNNING", "VERIFYING", "PAUSED"],
)
def test_no_longer_actionable_case_is_silent(fixture, status):
    fixture.register()
    fixture.case(status=status)
    fixture.event()
    assert fixture.dispatcher.flush("owner")["suppressed"] == 1
    assert fixture.sent == []


def test_superseded_version_is_silent(fixture):
    fixture.register()
    fixture.case(version=3)
    fixture.event(version=2)
    assert fixture.dispatcher.flush("owner")["suppressed"] == 1
    assert fixture.sent == []


@pytest.mark.parametrize(
    "kind,status",
    [
        ("ACTION_FAILED", "FAILED"),
        ("ACTION_FAILED", "PERMISSION_REVOKED"),
        ("PLAN_CHANGED", "DECISION_REQUIRED"),
    ],
)
def test_attention_kinds_send_only_for_matching_state(fixture, kind, status):
    fixture.register()
    fixture.case(status=status)
    fixture.event(kind=kind)
    assert fixture.dispatcher.flush("owner")["accepted"] == 1


def test_unregister_prevents_future_send(fixture):
    fixture.register()
    fixture.case()
    fixture.event()
    fixture.registry.unregister("owner", device_id=DEVICE)
    assert fixture.dispatcher.flush("owner")["suppressed"] == 1
    assert fixture.sent == []


def test_tombstones_do_not_hide_newly_registered_device(fixture):
    for index in range(6):
        old_device = f"device-{index:04d}"
        fixture.register(device=old_device)
        fixture.registry.unregister("owner", device_id=old_device)
    fixture.register(device="device-9999")
    fixture.case()
    fixture.event()
    assert fixture.dispatcher.flush("owner")["accepted"] == 1
    assert len(fixture.sent) == 1


def test_bearer_transferred_to_new_owner_does_not_receive_old_owner_alert(fixture):
    fixture.register()
    fixture.case()
    fixture.event()
    fixture.register("other")
    assert fixture.dispatcher.flush("owner")["suppressed"] == 1
    assert fixture.sent == []


def test_rotation_uses_only_current_token(fixture):
    fixture.register()
    fixture.case()
    fixture.event()
    new_token = "ExpoPushToken[synthetic_rotated_token]"
    fixture.register(token=new_token)
    fixture.dispatcher.flush("owner")
    assert [v["to"] for v in fixture.sent] == [new_token]


def test_one_bearer_registered_as_two_devices_receives_only_once(fixture):
    fixture.register()
    fixture.register(device="device-0002")
    fixture.case()
    fixture.event()
    fixture.dispatcher.flush("owner")
    assert len(fixture.sent) == 1


def test_device_not_registered_disables_only_matching_binding(fixture):
    fixture.register()
    fixture.case()
    fixture.event()
    fixture.dispatcher.transport = lambda _: PushOutcome(
        "REJECTED", "DEVICE_NOT_REGISTERED"
    )
    assert fixture.dispatcher.flush("owner")["rejected"] == 1
    assert fixture.registry.state("owner", device_id=DEVICE)["registered"] is False


@pytest.mark.parametrize("transfer", [False, True])
def test_delayed_stale_rejection_cannot_disable_rotated_or_new_owner_token(
    fixture, transfer
):
    fixture.register()
    fixture.case()
    fixture.event()

    def respond_after_change(message):
        fixture.register(
            "other" if transfer else "owner",
            token=TOKEN if transfer else "ExpoPushToken[synthetic_rotated_token]",
        )
        return PushOutcome("REJECTED", "DEVICE_NOT_REGISTERED")

    fixture.dispatcher.transport = respond_after_change
    fixture.dispatcher.flush("owner")
    assert (
        fixture.registry.state("other" if transfer else "owner", device_id=DEVICE)[
            "registered"
        ]
        is True
    )


def test_each_device_send_rechecks_case_and_revocation(fixture):
    fixture.register()
    fixture.register(
        device="device-0002", token="ExpoPushToken[synthetic_token_000002]"
    )
    fixture.case()
    fixture.event()

    def change_after_first(message):
        fixture.sent.append(message)
        fixture.registry.unregister("owner", device_id="device-0002")
        fixture.case(status="COMPLETED", version=3)
        return PushOutcome("ACCEPTED", "EXPO_TICKET_ACCEPTED")

    fixture.dispatcher.transport = change_after_first
    fixture.dispatcher.flush("owner")
    assert len(fixture.sent) == 1


def test_revocation_between_read_and_send_guard_prevents_send(fixture):
    fixture.register()
    fixture.case()
    fixture.event()
    real_permit = fixture.dispatcher._permit

    def revoke_then_permit(event, device):
        fixture.registry.unregister("owner", device_id=DEVICE)
        return real_permit(event, device)

    fixture.dispatcher._permit = revoke_then_permit
    assert fixture.dispatcher.flush("owner")["suppressed"] == 1
    assert fixture.sent == []


def test_owner_transfer_between_read_and_send_guard_prevents_send(fixture):
    fixture.register()
    fixture.case()
    fixture.event()
    real_permit = fixture.dispatcher._permit

    def transfer_then_permit(event, device):
        fixture.register("other")
        return real_permit(event, device)

    fixture.dispatcher._permit = transfer_then_permit
    assert fixture.dispatcher.flush("owner")["suppressed"] == 1
    assert fixture.sent == []


def test_case_completion_between_read_and_send_guard_prevents_send(fixture):
    fixture.register()
    fixture.case()
    fixture.event()
    real_permit = fixture.dispatcher._permit

    def complete_then_permit(event, device):
        fixture.case(status="COMPLETED", version=3)
        return real_permit(event, device)

    fixture.dispatcher._permit = complete_then_permit
    assert fixture.dispatcher.flush("owner")["suppressed"] == 1
    assert fixture.sent == []


def test_unknown_send_is_not_retried_and_exception_text_is_not_saved(fixture, caplog):
    fixture.register()
    fixture.case()
    event = fixture.event()

    def uncertain(message):
        fixture.sent.append(message)
        raise TimeoutError(TOKEN + " PRIVATE_SOURCE")

    fixture.dispatcher.transport = uncertain
    assert fixture.dispatcher.flush("owner")["unknown"] == 1
    fixture.dispatcher.flush("owner")
    assert len(fixture.sent) == 1
    saved = json.dumps(fixture.read(event))
    assert TOKEN not in saved and "PRIVATE_SOURCE" not in saved and caplog.text == ""


def test_interrupted_claim_becomes_unknown_without_resend(fixture):
    fixture.register()
    fixture.case()
    event = fixture.event()
    assert fixture.dispatcher._claim(event)
    assert fixture.dispatcher.flush("owner")["busy"] == 1
    fixture.now += 61
    assert fixture.dispatcher.flush("owner")["unknown"] == 1
    assert fixture.read(event)["status"] == {"S": "UNKNOWN"}
    assert fixture.sent == []


def test_crash_after_provider_acceptance_does_not_resend(fixture):
    fixture.register()
    fixture.case()
    event = fixture.event()
    original_finish = fixture.dispatcher._finish

    def crash(*args):
        raise RuntimeError("synthetic persistence outage")

    fixture.dispatcher._finish = crash
    with pytest.raises(RuntimeError):
        fixture.dispatcher.flush("owner")
    assert len(fixture.sent) == 1
    fixture.dispatcher._finish = original_finish
    fixture.now += 61
    assert fixture.dispatcher.flush("owner")["unknown"] == 1
    assert fixture.read(event)["status"] == {"S": "UNKNOWN"}
    assert len(fixture.sent) == 1


def test_expired_event_does_not_send_even_if_case_still_needs_attention(fixture):
    fixture.register()
    fixture.case()
    fixture.event()
    fixture.now += 31 * 86_400
    assert fixture.dispatcher.flush("owner")["suppressed"] == 1
    assert fixture.sent == []


def test_concurrent_dispatch_cannot_claim_same_event_twice(fixture):
    fixture.register()
    fixture.case()
    event = fixture.event()
    assert fixture.dispatcher._claim(event)
    assert fixture.dispatcher._claim(event) is None


def test_flush_is_bounded_to_five_events(fixture):
    fixture.register()
    for index in range(7):
        fixture.case(case=f"case-{index}")
        fixture.event(case=f"case-{index}")
    assert fixture.dispatcher.flush("owner")["accepted"] == 5
    assert len(fixture.sent) == 5
    assert fixture.dispatcher.flush("owner")["accepted"] == 2


class Response(io.BytesIO):
    status = 200


def transport_with(body, *, error=None, status=200):
    calls = []

    def open_request(request, *, timeout):
        calls.append((request, timeout))
        if error:
            raise error
        response = Response(body)
        response.status = status
        return response

    return ExpoPushTransport(opener=SimpleNamespace(open=open_request)), calls


@pytest.mark.parametrize(
    "ticket",
    [{"status": "ok", "id": "ticket-one"}, [{"status": "ok", "id": "ticket-one"}]],
)
def test_expo_fixed_endpoint_and_ticket_shapes(ticket):
    transport, calls = transport_with(json.dumps({"data": ticket}).encode())
    assert transport({"to": TOKEN}).status == "ACCEPTED"
    request, timeout = calls[0]
    assert request.full_url == EXPO_SEND_URL and request.method == "POST"
    assert timeout == 3 and len(calls) == 1


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            {"data": {"status": "error", "details": {"error": "DeviceNotRegistered"}}},
            "REJECTED",
        ),
        (
            {"data": {"status": "error", "details": {"error": "InvalidCredentials"}}},
            "REJECTED",
        ),
        ({"data": {"status": "ok"}}, "UNKNOWN"),
        ({"data": {"status": "ok", "id": ""}}, "UNKNOWN"),
        ({"data": {"status": "ok", "id": "ticket-one"}, "errors": [{}]}, "UNKNOWN"),
        ({"data": []}, "UNKNOWN"),
        ({"errors": [{"message": "PRIVATE_PROVIDER_ERROR"}]}, "UNKNOWN"),
        ([], "UNKNOWN"),
    ],
)
def test_expo_rejection_and_malformed_ticket_are_explicit(body, expected):
    transport, calls = transport_with(json.dumps(body).encode())
    assert transport({"to": TOKEN}).status == expected and len(calls) == 1


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, "REJECTED"),
        (401, "REJECTED"),
        (429, "REJECTED"),
        (500, "UNKNOWN"),
        (302, "UNKNOWN"),
    ],
)
def test_expo_http_errors_do_not_retry_or_echo_provider_body(status, expected, caplog):
    error = HTTPError(EXPO_SEND_URL, status, TOKEN, {}, None)
    transport, calls = transport_with(b"", error=error)
    outcome = transport({"to": TOKEN})
    assert outcome.status == expected and len(calls) == 1
    assert TOKEN not in repr(outcome) and caplog.text == ""


@pytest.mark.parametrize("body", [b"not-json", b"x" * 16_385])
def test_expo_invalid_or_oversize_response_remains_unknown(body):
    transport, _ = transport_with(body)
    assert transport({"to": TOKEN}).status == "UNKNOWN"


def test_expo_timeout_no_redirect_and_payload_bound():
    from quietpilot_worker.notifications import _NoRedirect

    assert (
        _NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test")
        is None
    )
    transport, calls = transport_with(b"", error=TimeoutError("PRIVATE"))
    assert transport({"to": TOKEN}).status == "UNKNOWN"
    assert len(calls) == 1
    assert transport({"to": TOKEN, "body": "x" * 5000}).code == "PAYLOAD_TOO_LARGE"
    assert len(calls) == 1
