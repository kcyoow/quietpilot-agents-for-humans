from __future__ import annotations

import hashlib
import json

import boto3
import pytest
from quietpilot_control_api.notifications import (
    MAX_DEVICES,
    PushTokenConflict,
    PushTokenInputError,
    PushTokenService,
)

moto = pytest.importorskip("moto")
TOKEN = "ExpoPushToken[synthetic_token_000001]"
DEVICE = "device-0001"


@pytest.fixture
def registry():
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
        db.create_table(
            TableName="notifications",
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
        yield PushTokenService("notifications", db, clock=lambda: 2_000_000_000), db


def register(service, owner="owner", **changes):
    return service.register(
        owner,
        **{
            "expo_push_token": TOKEN,
            "device_id": DEVICE,
            "app_version": "0.1.0",
            "platform": "android",
            **changes,
        },
    )


def test_registry_response_never_exposes_bearer_or_owner(registry, caplog, capsys):
    service, _ = registry
    result = register(service)
    assert result == service.state("owner", device_id=DEVICE)
    assert result["registered"] is True
    encoded = json.dumps(result)
    assert (
        TOKEN not in encoded
        and "token_hash" not in encoded
        and "user_id" not in encoded
    )
    assert caplog.text == "" and capsys.readouterr().out == ""


def test_another_owner_cannot_read_or_unregister_registration(registry):
    service, _ = registry
    register(service)
    assert service.state("other", device_id=DEVICE)["registered"] is False
    service.unregister("other", device_id=DEVICE)
    assert service.state("owner", device_id=DEVICE)["registered"] is True


def test_same_bearer_moving_owner_invalidates_old_owner_binding(registry):
    service, _ = registry
    register(service)
    register(service, "new-owner")
    assert service.state("owner", device_id=DEVICE)["registered"] is False
    assert service.state("new-owner", device_id=DEVICE)["registered"] is True
    service.unregister("owner", device_id=DEVICE)
    assert service.state("new-owner", device_id=DEVICE)["registered"] is True


def test_rotation_tombstones_old_binding_and_unregister_removes_bearer(registry):
    service, db = registry
    register(service)
    register(service, expo_push_token="ExponentPushToken[synthetic_token_000002]")
    digest = hashlib.sha256(TOKEN.encode()).hexdigest()
    old_binding = db.get_item(
        TableName="notifications",
        Key={"PK": {"S": f"PUSH_TOKEN#{digest}"}, "SK": {"S": "META"}},
    )["Item"]
    assert old_binding["enabled"] == {"BOOL": False}
    assert "expo_push_token" not in old_binding
    assert service.unregister("owner", device_id=DEVICE)["registered"] is False
    tombstone = db.get_item(
        TableName="notifications",
        Key={"PK": {"S": "USER#owner"}, "SK": {"S": f"PUSH_DEVICE#{DEVICE}"}},
    )["Item"]
    assert tombstone["enabled"] == {"BOOL": False}
    assert "expo_push_token" not in tombstone
    assert service.unregister("owner", device_id=DEVICE)["registered"] is False


def test_unregister_and_rotation_need_no_delete_permission(registry):
    service, db = registry
    real_write = db.transact_write_items
    actions = []

    def supported_write(**kwargs):
        for operation in kwargs["TransactItems"]:
            assert set(operation) <= {"Put", "Update", "ConditionCheck"}
            actions.extend(operation)
        return real_write(**kwargs)

    db.transact_write_items = supported_write
    register(service)
    register(service, expo_push_token="ExpoPushToken[synthetic_rotated_token]")
    service.unregister("owner", device_id=DEVICE)
    assert set(actions) == {"Put", "Update"}
    assert register(service)["registered"] is True


def test_tombstone_cas_cannot_disable_binding_transferred_after_read(registry):
    service, db = registry
    register(service)
    real_write = db.transact_write_items
    injected = False

    def transfer_before_tombstone(**kwargs):
        nonlocal injected
        if not injected:
            injected = True
            db.transact_write_items = real_write
            register(service, "new-owner")
        return real_write(**kwargs)

    db.transact_write_items = transfer_before_tombstone
    service.unregister("owner", device_id=DEVICE)
    assert service.state("owner", device_id=DEVICE)["registered"] is False
    assert service.state("new-owner", device_id=DEVICE)["registered"] is True


def test_ios_is_outside_current_public_contract(registry):
    service, db = registry
    with pytest.raises(PushTokenInputError):
        register(service, platform="ios")
    assert db.scan(TableName="notifications")["Count"] == 0


def test_device_cap_is_bounded_and_unregister_releases_slot(registry):
    service, _ = registry
    for index in range(MAX_DEVICES):
        register(
            service,
            device_id=f"device-{index:04d}",
            expo_push_token=f"ExpoPushToken[synthetic_token_{index:06d}]",
        )
    with pytest.raises(PushTokenConflict):
        register(service, device_id="device-9999")
    service.unregister("owner", device_id="device-0000")
    assert register(service, device_id="device-9999")["registered"] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"expo_push_token": "raw-token"},
        {"expo_push_token": "ExpoPushToken[has spaces]"},
        {"expo_push_token": "ExpoPushToken[short]"},
        {"expo_push_token": "ExpoPushToken[" + "a" * 201 + "]"},
        {"expo_push_token": TOKEN + "\n"},
        {"expo_push_token": None},
        {"device_id": "x"},
        {"device_id": "device#owner"},
        {"device_id": "a" * 129},
        {"device_id": None},
        {"app_version": "release\nsecret"},
        {"app_version": "a" * 65},
        {"platform": "web"},
        {"platform": []},
    ],
)
def test_invalid_shapes_fail_without_saving_or_echoing_input(registry, changes):
    service, db = registry
    with pytest.raises(PushTokenInputError) as caught:
        register(service, **changes)
    assert TOKEN not in str(caught.value)
    assert db.scan(TableName="notifications")["Count"] == 0


@pytest.mark.parametrize("owner", ["", " \t\n", None, "a" * 257])
def test_owner_matches_authoritative_subject_validation(registry, owner):
    service, _ = registry
    with pytest.raises(PushTokenInputError):
        register(service, owner)


@pytest.mark.parametrize(
    "owner", ["tenant|non-uuid-subject", " tenant|subject ", "a" * 256]
)
def test_authenticated_owner_is_preserved_exactly(registry, owner):
    service, db = registry
    assert register(service, owner)["registered"] is True
    record = db.get_item(
        TableName="notifications",
        Key={"PK": {"S": f"USER#{owner}"}, "SK": {"S": f"PUSH_DEVICE#{DEVICE}"}},
    )["Item"]
    assert record["user_id"] == {"S": owner}
    if owner != owner.strip():
        assert service.state(owner.strip(), device_id=DEVICE)["registered"] is False


def test_identical_registration_is_read_only_and_preserves_generation(
    registry, monkeypatch
):
    service, db = registry
    original = register(service)
    before = db.scan(TableName="notifications")["Items"]

    def unexpected_write(**kwargs):
        raise AssertionError("identical registration must not write")

    monkeypatch.setattr(db, "transact_write_items", unexpected_write)
    assert register(service) == original
    assert db.scan(TableName="notifications")["Items"] == before


@pytest.mark.parametrize("change", ["unregister", "owner_transfer"])
def test_identical_registration_does_not_undo_concurrent_revocation_or_transfer(
    registry, monkeypatch, change
):
    service, _ = registry
    register(service)
    read_state = service.state

    def changed_state(owner, *, device_id):
        if change == "unregister":
            service.unregister(owner, device_id=device_id)
        else:
            register(service, "new-owner")
        return read_state(owner, device_id=device_id)

    monkeypatch.setattr(service, "state", changed_state)
    assert register(service)["registered"] is False
    assert read_state("owner", device_id=DEVICE)["registered"] is False
    if change == "owner_transfer":
        assert read_state("new-owner", device_id=DEVICE)["registered"] is True


@pytest.mark.parametrize("change", ["unregister", "owner_transfer"])
def test_registration_update_cas_does_not_retry_over_newer_owner_or_revocation(
    registry, monkeypatch, change
):
    service, db = registry
    register(service)
    transact = db.transact_write_items
    modified = False

    def change_before_commit(**kwargs):
        nonlocal modified
        if not modified:
            modified = True
            monkeypatch.setattr(db, "transact_write_items", transact)
            if change == "unregister":
                service.unregister("owner", device_id=DEVICE)
            else:
                register(service, "new-owner")
        return transact(**kwargs)

    monkeypatch.setattr(db, "transact_write_items", change_before_commit)
    with pytest.raises(PushTokenConflict):
        register(service, app_version="0.1.1")
    assert service.state("owner", device_id=DEVICE)["registered"] is False
    if change == "owner_transfer":
        assert service.state("new-owner", device_id=DEVICE)["registered"] is True


def test_caller_body_cannot_supply_another_owner(registry):
    service, _ = registry
    with pytest.raises(TypeError):
        register(service, owner_id="other")


def test_concurrent_registry_write_retries_cas_without_losing_device(registry):
    service, db = registry
    real_write = db.transact_write_items
    injected = False

    def competing_write(**kwargs):
        nonlocal injected
        if not injected:
            injected = True
            db.transact_write_items = real_write
            register(
                service,
                device_id="device-0002",
                expo_push_token="ExpoPushToken[synthetic_token_000002]",
            )
        return real_write(**kwargs)

    db.transact_write_items = competing_write
    register(service)
    assert service.state("owner", device_id=DEVICE)["registered"] is True
    assert service.state("owner", device_id="device-0002")["registered"] is True
