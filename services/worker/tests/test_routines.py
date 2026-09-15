from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from quietpilot_control_api.workspace import DynamoWorkspaceStore
from quietpilot_worker import consumer
from quietpilot_worker.routines import RoutineDispatchError, RoutineProcessor

from tests.test_routine_flow import (
    CASE,
    NOW,
    TABLE,
    USER,
    activate,
    get,
    j,
    key,
    messages,
    put,
    rows,
    seed_candidate,
    seed_source,
)
from tests.test_routine_flow import routine_env as shared_routine_env

routine_env = shared_routine_env


def setup(env, *, deadline=True):
    seed_source(env, deadline=deadline)
    routine = activate(env)
    env.now += timedelta(minutes=1)
    return routine


def test_verified_appointment_recipe_also_creates_only_a_preparation_case(routine_env):
    env = routine_env
    setup(env, deadline=False)
    seed_candidate(env, kind="APPOINTMENT")
    assert env.processor.process(USER)["created"] == 1
    event = messages(env)[0]
    meta = get(env, f"CASE#{event['payload']['case_id']}", "META")
    actions = json.loads(meta["requested_actions_json"]["S"])
    assert actions[0]["verb"] == "prepare_reminder"
    assert actions[0]["required_scopes"] == []
    assert meta["required_capabilities"] == {
        "L": [{"S": "quietpilot.reminder.prepare"}]
    }


@pytest.mark.parametrize(
    "boundary",
    [
        "old",
        "equal_activation",
        "future",
        "domain",
        "subdomain",
        "kind",
        "hidden",
        "converted",
        "candidate_profile",
        "candidate_epoch",
        "candidate_scan",
        "source_owner",
        "source_time_missing",
        "source_time_duplicate",
        "external_scope",
        "external_verb",
        "external_parameters",
        "unselected_source",
        "multiple_actions",
    ],
)
def test_only_new_matching_owned_safe_candidates_are_consumed(routine_env, boundary):
    env = routine_env
    setup(env)
    received = (
        NOW - timedelta(days=1)
        if boundary == "old"
        else NOW
        if boundary == "equal_activation"
        else env.now + timedelta(days=1)
        if boundary == "future"
        else None
    )
    candidate_id, reference = seed_candidate(
        env,
        received=received,
        domain="other.example.invalid"
        if boundary == "domain"
        else "child.sender.example.invalid"
        if boundary == "subdomain"
        else "sender.example.invalid",
        kind="APPOINTMENT" if boundary == "kind" else "DEADLINE",
    )
    candidate = get(env, f"USER#{USER}", f"CANDIDATE#{candidate_id}")
    source = get(env, f"USER#{USER}", f"EVIDENCE#{reference}")
    if boundary in {"hidden", "converted"}:
        candidate["status"] = {"S": boundary.upper()}
    elif boundary == "candidate_profile":
        candidate["mail_profile_version"] = {"N": "2"}
    elif boundary == "candidate_epoch":
        candidate["mail_connection_id"] = {"S": "old-epoch"}
    elif boundary == "candidate_scan":
        candidate["mail_scan_id"] = {"S": "old-scan"}
    elif boundary == "source_owner":
        source["user_id"] = {"S": "another-owner"}
    elif boundary == "source_time_missing":
        source["facts"]["L"] = [
            value
            for value in source["facts"]["L"]
            if not value["S"].startswith("received_at_unix_ms=")
        ]
    elif boundary == "source_time_duplicate":
        source["facts"]["L"].append(source["facts"]["L"][1])
    elif boundary in {
        "external_scope",
        "external_verb",
        "external_parameters",
        "unselected_source",
        "multiple_actions",
    }:
        actions = json.loads(candidate["proposed_actions_json"]["S"])
        if boundary == "external_scope":
            actions[0]["required_scopes"] = ["calendar.events.owned"]
        elif boundary == "external_verb":
            actions[0].update(connector="google", verb="calendar_event_create")
        elif boundary == "external_parameters":
            actions[0]["parameters"]["approved"] = True
        elif boundary == "unselected_source":
            actions[0]["parameters"]["source_ref"] = "gmail:" + "b" * 64
        else:
            actions *= 2
        candidate["proposed_actions_json"] = j(actions)
    put(env, candidate)
    put(env, source)
    assert env.processor.process(USER)["created"] == 0
    assert not messages(env)
    assert get(env, f"USER#{USER}", f"CANDIDATE#{candidate_id}") == candidate


@pytest.mark.parametrize(
    "boundary",
    [
        "paused",
        "connection",
        "account",
        "profile",
        "source_version",
        "source_unverified",
        "suppression",
    ],
)
def test_paused_or_changed_context_does_not_create_future_cases(routine_env, boundary):
    env = routine_env
    routine = setup(env)
    seed_candidate(env)
    if boundary == "paused":
        env.api.pause(USER, routine_id=routine["routine_id"], expected_version=2)
    elif boundary in {"connection", "account"}:
        item = get(env, f"USER#{USER}", "CONNECTION#google")
        item["mail_connection_id" if boundary == "connection" else "account_hash"] = {
            "S": "changed" if boundary == "connection" else "c" * 64
        }
        put(env, item)
    elif boundary == "profile":
        item = get(env, f"USER#{USER}", "MAIL_INTERESTS#google")
        profile = json.loads(item["profile_json"]["S"])
        profile["version"] = 2
        item.update(profile_version={"N": "2"}, profile_json=j(profile))
        put(env, item)
    elif boundary == "source_version":
        item = get(env, f"CASE#{CASE}", "META")
        item["version"] = {"N": "8"}
        put(env, item)
    elif boundary == "source_unverified":
        item = get(env, f"CASE#{CASE}", "ACTION#operation-original")
        item["verified"] = {"BOOL": False}
        put(env, item)
    else:
        suppression_id = hashlib.sha256(f"{USER}:deadlines".encode()).hexdigest()[:24]
        put(
            env,
            {
                **key(f"USER#{USER}", f"SUPPRESSION#{suppression_id}"),
                "user_id": {"S": USER},
                "group_id": {"S": "deadlines"},
                "version": {"N": "1"},
                "active": {"BOOL": True},
            },
        )
    assert env.processor.process(USER)["created"] == 0
    assert not messages(env)


def test_pause_and_reactivation_do_not_run_mail_received_during_the_pause(routine_env):
    env = routine_env
    routine = setup(env)
    paused = env.api.pause(USER, routine_id=routine["routine_id"], expected_version=2)[
        "routine"
    ]
    seed_candidate(env)
    env.now += timedelta(minutes=1)
    env.api.activate(USER, routine_id=paused["routine_id"], expected_version=3)
    assert env.processor.process(USER)["created"] == 0


def test_queue_failure_recovers_committed_case_before_new_work_even_after_pause(
    routine_env, monkeypatch
):
    env = routine_env
    routine = setup(env)
    candidate_id, _ = seed_candidate(env)
    send = env.queue.send

    def fail(**kwargs):
        raise RuntimeError("synthetic queue failure")

    monkeypatch.setattr(env.queue, "send", fail)
    with pytest.raises(RoutineDispatchError):
        env.processor.process(USER)
    assert get(env, f"USER#{USER}", f"CANDIDATE#{candidate_id}")["status"] == {
        "S": "CONVERTED"
    }
    markers = [
        row
        for row in rows(env, f"USER#{USER}")
        if row["SK"]["S"].startswith("ROUTINE_DISPATCH#")
    ]
    assert len(markers) == 1
    case_id = markers[0]["case_id"]["S"]
    env.api.pause(USER, routine_id=routine["routine_id"], expected_version=2)
    monkeypatch.setattr(env.queue, "send", send)
    result = env.processor.process(USER)
    assert result["created"] == 0 and result["recovered"] == result["dispatched"] == 1
    assert messages(env)[0]["payload"] == {"case_id": case_id, "plan_version": 1}
    assert get(env, f"CASE#{case_id}", "META")["dispatch_pending"] == {"BOOL": False}
    assert not [
        row
        for row in rows(env, f"USER#{USER}")
        if row["SK"]["S"].startswith("ROUTINE_DISPATCH#")
    ]
    assert env.processor.process(USER)["created"] == 0


@pytest.mark.parametrize(
    "boundary", ["pause", "profile", "connection", "suppression", "manual"]
)
def test_atomic_guards_block_creation_when_context_changes_during_consume(
    routine_env, monkeypatch, boundary
):
    env = routine_env
    routine = setup(env)
    candidate_id, _ = seed_candidate(env)
    transact, changed = env.db.transact_write_items, []

    def interleave(**kwargs):
        if not changed:
            changed.append(True)
            if boundary == "pause":
                env.api.pause(
                    USER, routine_id=routine["routine_id"], expected_version=2
                )
            elif boundary == "profile":
                item = get(env, f"USER#{USER}", "MAIL_INTERESTS#google")
                item["profile_version"] = {"N": "2"}
                put(env, item)
            elif boundary == "connection":
                item = get(env, f"USER#{USER}", "CONNECTION#google")
                item["mail_connection_id"] = {"S": "changed"}
                put(env, item)
            elif boundary == "suppression":
                suppression_id = hashlib.sha256(
                    f"{USER}:deadlines".encode()
                ).hexdigest()[:24]
                put(
                    env,
                    {
                        **key(f"USER#{USER}", f"SUPPRESSION#{suppression_id}"),
                        "user_id": {"S": USER},
                        "group_id": {"S": "deadlines"},
                        "version": {"N": "1"},
                        "active": {"BOOL": True},
                    },
                )
            else:
                result, created = DynamoWorkspaceStore(
                    TABLE, env.db
                ).convert_candidates(
                    USER,
                    case_id="manual-winner",
                    candidate_ids=[candidate_id],
                    expected_versions=[1],
                    now=env.now.isoformat(),
                )
                assert created and result["case_id"] == "manual-winner"
        return transact(**kwargs)

    monkeypatch.setattr(env.db, "transact_write_items", interleave)
    result = env.processor.process(USER)
    assert result["created"] == 0 and result["conflicts"] == 1
    assert not messages(env)
    assert not [
        row
        for row in rows(env, f"USER#{USER}")
        if row["SK"]["S"].startswith("ROUTINE_DISPATCH#")
    ]


def test_another_owner_cannot_trigger_or_dispatch_this_users_records(routine_env):
    env = routine_env
    setup(env)
    candidate_id, _ = seed_candidate(env)
    assert env.processor.process("other-owner")["created"] == 0
    assert get(env, f"USER#{USER}", f"CANDIDATE#{candidate_id}")["status"] == {
        "S": "VISIBLE"
    }


def test_candidate_query_limit_is_explicit_in_result(routine_env):
    env = routine_env
    setup(env)
    for index in range(51):
        candidate_id, _ = seed_candidate(env, name=f"overflow-{index}")
        candidate = get(env, f"USER#{USER}", f"CANDIDATE#{candidate_id}")
        candidate["status"] = {"S": "HIDDEN"}
        put(env, candidate)
    result = env.processor.process(USER)
    assert result["limit_reached"] is True and result["candidates_checked"] == 50
    assert result["created"] == 0


def test_unrelated_group_overflow_cannot_fail_completed_mail_post_processing(
    routine_env, monkeypatch
):
    env = routine_env
    setup(env)  # Only a DEADLINE routine is active.
    for index in range(51):
        seed_candidate(env, name=f"unrelated-{index}", kind="APPOINTMENT")
    completed_mail = []
    monkeypatch.setattr(
        consumer,
        "default_google_job_processor",
        lambda: SimpleNamespace(process=lambda event: completed_mail.append(event)),
    )
    monkeypatch.setattr(
        "quietpilot_worker.routines.default_routine_processor", lambda: env.processor
    )
    event = {"event_type": "MAIL_SCAN_CONTINUATION", "user_id": USER}
    consumer._foundation_processor(event)
    assert completed_mail == [event]
    result = env.processor.process(USER)
    assert result["limit_reached"] is False
    assert (
        result["candidates_checked"] == result["created"] == result["dispatched"] == 0
    )
    assert not messages(env)


def test_remaining_pending_dispatches_take_priority_over_new_triggers(monkeypatch):
    def prefix(user_id, prefix, limit):
        assert user_id == USER and prefix == "ROUTINE_DISPATCH#" and limit == 32
        return [{"marker": index} for index in range(32)], True

    def no_new_work(_user_id):
        raise AssertionError("New triggers started before pending recovery finished")

    processor = RoutineProcessor(
        SimpleNamespace(prefix=prefix, current=no_new_work),
        SimpleNamespace(),
        clock=lambda: NOW,
    )
    monkeypatch.setattr(processor, "_dispatch", lambda *_args: True)
    result = processor.process(USER)
    assert result["recovered"] == result["dispatched"] == 32
    assert result["limit_reached"] is True and result["created"] == 0
