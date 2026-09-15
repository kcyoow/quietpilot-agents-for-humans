"""Manual continuation dispatch with real offline Dynamo conditions."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import boto3
import pytest
from quietpilot_control_api.mail import (
    DynamoMailStore,
    MailConflict,
    MailInterestService,
    empty_state,
)

moto = pytest.importorskip("moto")
TABLE = "mail-scan-resume"
OWNER = "owner"
SCAN = "a" * 32
EPOCH = "connection-one"


def key(owner=OWNER, kind="MAIL_INTERESTS#google"):
    return {"PK": {"S": f"USER#{owner}"}, "SK": {"S": kind}}


def encoded(value):
    return {"S": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}


def continuation(step=1):
    token = f"synthetic-page-{step}"
    return {
        "event_type": "MAIL_SCAN_CONTINUATION",
        "payload": {
            "request_id": SCAN,
            "profile_version": 1,
            "step": step,
            "page_token": token,
            "page_token_hashes": [
                hashlib.sha256(f"synthetic-page-{index}".encode()).hexdigest()
                for index in range(1, step + 1)
            ],
        },
    }


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
        db.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"} for name in ("PK", "SK")
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        sent = []
        queue = SimpleNamespace(send=lambda **values: sent.append(values))
        store = DynamoMailStore(TABLE, db)
        yield SimpleNamespace(
            db=db,
            store=store,
            queue=queue,
            sent=sent,
            service=MailInterestService(store, queue),
        )


def saved(env, owner=OWNER):
    return env.db.get_item(TableName=TABLE, Key=key(owner), ConsistentRead=True)["Item"]


def put(env, item):
    env.db.put_item(TableName=TABLE, Item=item)


def seed(env, *, status="ERROR", step=1, owner=OWNER, rows=0):
    state = empty_state()
    state["profile"].update(tags=["학교"], version=1)
    state["scan"].update(
        status=status,
        scan_id=SCAN,
        profile_version=1,
        processed_count=32,
        matched_count=rows,
        error_code="MAIL_SCAN_FAILED",
    )
    item = {
        **key(owner),
        **{f"{name}_json": encoded(value) for name, value in state.items()},
        "profile_version": {"N": "1"},
        "scan_id": {"S": SCAN},
        "scan_connection_id": {"S": EPOCH},
        "scan_step": {"N": str(step)},
        "scan_next_json": encoded(continuation(step)),
    }
    put(env, item)
    put(
        env,
        {
            **key(owner, "CONNECTION#google"),
            "status": {"S": "CONNECTED"},
            "mail_connection_id": {"S": EPOCH},
        },
    )
    for index in range(rows):
        put(
            env,
            {
                **key(owner, f"MAIL_RESULT#{SCAN}#{index:03}"),
                "result_json": encoded(
                    {
                        "evidence_ref": f"gmail:synthetic-{index:03}",
                        "title": f"합성 안내 {index}",
                        "summary": "확인된 정보",
                        "reason": "관심 일치",
                        "sender_domain": "example.invalid",
                        "received_at": None,
                        "matched_tags": ["학교"],
                    }
                ),
            },
        )
    return item


def test_error_resume_preserves_rows_counts_cursor_and_exact_continuation(env):
    before = seed(env, rows=31)
    first = env.service.results(OWNER)
    assert first["status"] == "ERROR" and len(first["items"]) == 30
    old_cursor = first["next_cursor"]
    current = env.service.scan(OWNER)
    after = saved(env)
    assert current["scan"] == {
        **json.loads(before["scan_json"]["S"]),
        "status": "PENDING",
        "error_code": None,
    }
    assert {key: value for key, value in after.items() if key != "scan_json"} == {
        key: value for key, value in before.items() if key != "scan_json"
    }
    assert env.sent == [{"user_id": OWNER, **continuation()}]
    assert env.service.results(OWNER)["items"] == first["items"]
    assert len(env.service.results(OWNER, old_cursor)["items"]) == 1
    assert set(current) == {"profile", "recommendations", "scan"}


def test_pending_retry_dispatches_current_step_without_restarting_or_writing(env):
    before = seed(env, status="PENDING", step=2)
    env.service.scan(OWNER)
    assert env.sent == [{"user_id": OWNER, **continuation(2)}]
    assert saved(env) == before


@pytest.mark.parametrize("status", ["ERROR", "PENDING"])
@pytest.mark.parametrize(
    "invalid",
    [
        "event",
        "request",
        "version",
        "boolean_version",
        "step",
        "boolean_step",
        "empty_token",
        "whitespace_token",
        "large_token",
        "hash",
        "hash_count",
        "duplicate_hash",
        "owner_field",
        "missing",
        "not_object",
        "extra_field",
    ],
)
def test_invalid_continuation_is_rejected_without_reset_or_dispatch(
    env, status, invalid
):
    item = seed(env, status=status, step=2)
    work = continuation(2)
    payload = work["payload"]
    if invalid == "event":
        work["event_type"] = "MAIL_SCAN_REQUESTED"
    elif invalid == "request":
        payload["request_id"] = "b" * 32
    elif invalid == "version":
        payload["profile_version"] = 2
    elif invalid == "boolean_version":
        payload["profile_version"] = True
    elif invalid == "step":
        payload["step"] = 1
    elif invalid == "boolean_step":
        payload["step"] = True
    elif invalid == "empty_token":
        payload["page_token"] = ""
    elif invalid == "whitespace_token":
        payload["page_token"] = "bad token"
    elif invalid == "large_token":
        payload["page_token"] = "x" * 2049
    elif invalid == "hash":
        payload["page_token_hashes"][-1] = "0" * 64
    elif invalid == "hash_count":
        payload["page_token_hashes"] = payload["page_token_hashes"][-1:]
    elif invalid == "duplicate_hash":
        payload["page_token_hashes"] = payload["page_token_hashes"][-1:] * 2
    elif invalid == "owner_field":
        payload["user_id"] = "another-owner"
    elif invalid == "missing":
        work = None
    elif invalid == "not_object":
        work = []
    else:
        work["extra"] = "unexpected"
    item["scan_next_json"] = encoded(work)
    put(env, item)
    with pytest.raises(MailConflict):
        env.service.scan(OWNER)
    assert saved(env) == item and not env.sent


@pytest.mark.parametrize(
    "boundary", ["ready", "completed_error", "profile", "connection", "step_zero"]
)
def test_ineligible_resume_starts_a_fresh_generation(env, boundary):
    item = seed(env)
    scan = json.loads(item["scan_json"]["S"])
    if boundary in {"ready", "completed_error"}:
        scan["status"] = "READY" if boundary == "ready" else "ERROR"
        scan["completed_at"] = "2026-09-14T00:00:00Z"
    elif boundary == "profile":
        profile = json.loads(item["profile_json"]["S"])
        profile["version"] = 2
        item.update(profile_json=encoded(profile), profile_version={"N": "2"})
    elif boundary == "connection":
        put(
            env,
            {
                **key(OWNER, "CONNECTION#google"),
                "status": {"S": "CONNECTED"},
                "mail_connection_id": {"S": "new-connection"},
            },
        )
    else:
        item.update(scan_step={"N": "0"}, scan_next_json=encoded(None))
    item["scan_json"] = encoded(scan)
    put(env, item)
    current = env.service.scan(OWNER)
    assert current["scan"]["scan_id"] != SCAN
    assert current["scan"]["processed_count"] == current["scan"]["matched_count"] == 0
    assert saved(env)["scan_step"] == {"N": "0"}
    assert saved(env)["scan_next_json"] == encoded(None)
    assert env.sent[0]["event_type"] == "MAIL_SCAN_REQUESTED"


def race(env, boundary):
    item = saved(env)
    scan = json.loads(item["scan_json"]["S"])
    if boundary == "connection":
        put(
            env,
            {
                **key(OWNER, "CONNECTION#google"),
                "status": {"S": "CONNECTED"},
                "mail_connection_id": {"S": "reconnected"},
            },
        )
        return item
    if boundary == "complete":
        scan.update(status="READY", completed_at="2026-09-14T00:00:00Z")
    elif boundary == "profile":
        profile = json.loads(item["profile_json"]["S"])
        profile["version"] = 2
        item.update(profile_json=encoded(profile), profile_version={"N": "2"})
    elif boundary == "scan":
        scan["scan_id"] = "b" * 32
        item.update(
            scan_id={"S": "b" * 32}, scan_step={"N": "0"}, scan_next_json=encoded(None)
        )
    elif boundary == "cursor":
        item["scan_next_json"] = encoded(continuation(2))
    else:
        scan.update(status="PENDING", processed_count=64)
        item.update(scan_step={"N": "2"}, scan_next_json=encoded(continuation(2)))
    item["scan_json"] = encoded(scan)
    put(env, item)
    return item


@pytest.mark.parametrize(
    "boundary", ["complete", "advance", "profile", "scan", "connection", "cursor"]
)
def test_resume_cas_cannot_overwrite_a_concurrent_change(env, monkeypatch, boundary):
    seed(env)
    transact = env.db.transact_write_items
    raced = []

    def interleave(**kwargs):
        if not raced:
            raced.append(race(env, boundary))
        return transact(**kwargs)

    monkeypatch.setattr(env.db, "transact_write_items", interleave)
    with pytest.raises(MailConflict):
        env.service.scan(OWNER)
    assert saved(env) == raced[0] and not env.sent


def test_queue_failure_can_retry_the_same_continuation_again(env):
    before = seed(env, rows=1)

    def fail(**kwargs):
        raise RuntimeError("synthetic queue failure")

    env.queue.send = fail
    with pytest.raises(RuntimeError):
        env.service.scan(OWNER)
    failed = saved(env)
    assert (
        json.loads(failed["scan_json"]["S"])["error_code"] == "MAIL_QUEUE_UNAVAILABLE"
    )
    assert failed["scan_next_json"] == before["scan_next_json"]
    assert failed["scan_step"] == before["scan_step"]
    env.queue.send = lambda **kwargs: env.sent.append(kwargs)
    env.service.scan(OWNER)
    assert env.sent == [{"user_id": OWNER, **continuation()}]
    assert env.service.results(OWNER)["items"]


@pytest.mark.parametrize(
    "boundary", ["complete", "advance", "profile", "scan", "connection", "cursor"]
)
def test_queue_failure_cannot_clobber_progress_after_dispatch(env, boundary):
    seed(env)
    raced = []

    def fail(**kwargs):
        assert kwargs == {"user_id": OWNER, **continuation()}
        raced.append(race(env, boundary))
        raise RuntimeError("synthetic uncertain send")

    env.queue.send = fail
    with pytest.raises(RuntimeError):
        env.service.scan(OWNER)
    assert saved(env) == raced[0]


def test_owner_and_result_cursor_remain_bound_during_resume(env):
    original = seed(env, rows=31)
    cursor = env.service.results(OWNER)["next_cursor"]
    seed(env, owner="other", rows=31)
    env.service.scan("other")
    assert saved(env) == original
    assert env.sent == [{"user_id": "other", **continuation()}]
    with pytest.raises(MailConflict):
        env.service.results("other", cursor)


@pytest.mark.parametrize(
    "boundary", ["complete", "advance", "profile", "scan", "connection"]
)
def test_dispatch_rereads_current_work_after_the_resume_commit(
    env, monkeypatch, boundary
):
    seed(env)
    scan_work = env.store.scan_work

    def interleave(*args):
        race(env, boundary)
        return scan_work(*args)

    monkeypatch.setattr(env.store, "scan_work", interleave)
    env.service.scan(OWNER)
    assert env.sent == (
        [{"user_id": OWNER, **continuation(2)}] if boundary == "advance" else []
    )
