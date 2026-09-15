from __future__ import annotations

from types import SimpleNamespace

import boto3
import pytest
from quietpilot_control_api.calendar_connection import CalendarConnection
from quietpilot_control_api.connections import (
    ConnectionFlowConflict,
    ConnectionInputError,
    DynamoConnectionStore,
    GoogleConnectionService,
    _oauth_key,
    _oauth_return_key,
)

moto = pytest.importorskip("moto")


class Runtime:
    def __init__(self):
        self.calls = []
        self.account = "a" * 64
        self.authorized = False

    def invoke(self, user, payload):
        self.calls.append(payload)
        if payload["operation"] == "GOOGLE_CALENDAR_STATUS":
            return {"status": "CONNECTED", "account_hash": self.account}
        if self.authorized:
            return {"status": "TOKEN_AVAILABLE"}
        return {
            "status": "AUTHORIZATION_REQUIRED",
            "authorization_url": "https://accounts.google.com/o/oauth2/auth?fixture=1",
            "session_uri": "urn:ietf:params:oauth:request_uri:calendar-fixture",
        }


@pytest.fixture
def environment():
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
        db.create_table(
            TableName="mail",
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
        key = {"PK": {"S": "USER#owner"}, "SK": {"S": "CONNECTION#google"}}
        db.put_item(
            TableName="mail",
            Item={
                **key,
                "status": {"S": "CONNECTED"},
                "mail_connection_id": {"S": "generation-one"},
                "account_hash": {"S": "a" * 64},
            },
        )
        runtime = Runtime()
        calendar = CalendarConnection(
            "mail", db, runtime, "https://example.test/oauth/google/callback"
        )
        yield db, key, runtime, calendar


def test_consent_is_incremental_and_completion_preserves_mail_generation(environment):
    db, key, runtime, calendar = environment
    original = db.get_item(TableName="mail", Key=key)["Item"]
    result = calendar.authorize("owner")
    state = runtime.calls[0]["state"]
    assert "authorization_url" in result and "session_uri" not in result
    assert calendar.state("owner")["status"] == "DISCONNECTED"
    calendar.complete("owner", state)
    assert calendar.state("owner")["status"] == "CONNECTED"
    assert db.get_item(TableName="mail", Key=key)["Item"] == original
    assert [call["operation"] for call in runtime.calls] == [
        "GOOGLE_CALENDAR_AUTHORIZE",
        "GOOGLE_CALENDAR_STATUS",
    ]


@pytest.mark.parametrize("change", ["owner", "generation", "account"])
def test_calendar_cannot_attach_to_another_owner_generation_or_account(
    environment, change
):
    db, key, runtime, calendar = environment
    calendar.authorize("owner")
    state = runtime.calls[0]["state"]
    if change == "generation":
        db.update_item(
            TableName="mail",
            Key=key,
            UpdateExpression="SET mail_connection_id=:value",
            ExpressionAttributeValues={":value": {"S": "new-generation"}},
        )
    elif change == "account":
        runtime.account = "b" * 64
    with pytest.raises((ConnectionFlowConflict, ConnectionInputError)):
        calendar.complete("stranger" if change == "owner" else "owner", state)
    assert calendar.state("owner")["status"] == "DISCONNECTED"


def test_google_callback_completes_calendar_without_restart_or_rescan(environment):
    db, key, runtime, calendar = environment

    class Identity:
        def complete(self, user, session):
            assert user == "owner"

    class Queue:
        def send(self, **payload):
            pytest.fail("Calendar consent must not restart Gmail")

    store = DynamoConnectionStore("mail", db)
    service = GoogleConnectionService(
        store, runtime, Identity(), Queue(), calendar.callback_url, calendar=calendar
    )
    service.authorize("owner", capability="calendar")
    state = runtime.calls[0]["state"]
    flow = store.get_oauth_flow(state)
    assert flow["purpose"] == "calendar"
    code = "c" * 43
    db.put_item(
        TableName="mail",
        Item={
            **_oauth_return_key(code),
            "user_id": {"S": "owner"},
            "state": {"S": state},
            "session_uri": {"S": flow["session_uri"]},
            "status": {"S": "PENDING"},
            "expiresAt": {"N": str(flow["expires_at"])},
        },
    )
    result = service.complete("owner", code=code)
    assert result["status"] == "CONNECTED"
    assert result["calendar"]["status"] == "CONNECTED"
    assert service.complete("owner", code=code) == result
    assert (
        db.get_item(TableName="mail", Key=key)["Item"]["mail_connection_id"]["S"]
        == "generation-one"
    )


def test_reconnected_gmail_invalidates_old_calendar_consent(environment):
    db, key, runtime, calendar = environment
    runtime.authorized = True
    calendar.authorize("owner")
    assert calendar.state("owner")["status"] == "CONNECTED"
    db.update_item(
        TableName="mail",
        Key=key,
        UpdateExpression="SET mail_connection_id=:value",
        ExpressionAttributeValues={":value": {"S": "next-generation"}},
    )
    assert calendar.state("owner")["status"] == "DISCONNECTED"


@pytest.mark.parametrize("change", ["generation", "expired"])
def test_invalidated_calendar_flow_cannot_complete_identity(environment, change):
    db, key, runtime, calendar = environment
    calls = []
    store = DynamoConnectionStore("mail", db)
    service = GoogleConnectionService(
        store,
        runtime,
        SimpleNamespace(complete=lambda *args: calls.append(args)),
        SimpleNamespace(send=lambda **kwargs: pytest.fail("Unexpected scan")),
        calendar.callback_url,
        calendar=calendar,
    )
    service.authorize("owner", capability="calendar")
    state = runtime.calls[0]["state"]
    flow = store.get_oauth_flow(state)
    code = "d" * 43
    db.put_item(
        TableName="mail",
        Item={
            **_oauth_return_key(code),
            "user_id": {"S": "owner"},
            "state": {"S": state},
            "session_uri": {"S": flow["session_uri"]},
            "status": {"S": "PENDING"},
            "expiresAt": {"N": str(flow["expires_at"])},
        },
    )
    if change == "generation":
        db.update_item(
            TableName="mail",
            Key=key,
            UpdateExpression="SET mail_connection_id=:next",
            ExpressionAttributeValues={":next": {"S": "epoch-next"}},
        )
    else:
        db.update_item(
            TableName="mail",
            Key=_oauth_key(state),
            UpdateExpression="SET expiresAt=:expired",
            ExpressionAttributeValues={":expired": {"N": "1"}},
        )
    with pytest.raises(ConnectionFlowConflict):
        service.complete("owner", code=code)
    assert calls == []


def test_calendar_readiness_requires_the_verified_scope(environment):
    db, _key, runtime, calendar = environment
    runtime.authorized = True
    calendar.authorize("owner")
    db.update_item(
        TableName="mail",
        Key={"PK": {"S": "USER#owner"}, "SK": {"S": "CONNECTION#google-calendar"}},
        UpdateExpression="SET granted_scopes=:empty",
        ExpressionAttributeValues={":empty": {"L": []}},
    )
    assert calendar.state("owner")["status"] == "DISCONNECTED"
