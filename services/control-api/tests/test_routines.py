from __future__ import annotations

import json
from datetime import timedelta

import pytest
from quietpilot_control_api.routines import (
    DEADLINE_DESCRIPTION,
    LEGACY_DEADLINE_DESCRIPTION,
    RoutineConflict,
    RoutineInputError,
    RoutineLimitError,
    RoutineNotFound,
    RoutineNotReady,
)

from tests.test_routine_flow import (
    ACCOUNT,
    CASE,
    EPOCH,
    USER,
    activate,
    get,
    j,
    key,
    put,
    rows,
    seed_source,
)
from tests.test_routine_flow import routine_env as shared_routine_env

routine_env = shared_routine_env


@pytest.mark.parametrize(
    "description", [DEADLINE_DESCRIPTION, LEGACY_DEADLINE_DESCRIPTION]
)
def test_english_routine_preserves_new_and_legacy_calendar_marker_plans(
    routine_env, monkeypatch, description
):
    monkeypatch.setattr("tests.test_routine_flow.DEADLINE_DESCRIPTION", description)
    seed_source(routine_env)
    original = get(routine_env, f"CASE#{CASE}", "PLAN#000001")
    routine = routine_env.api.propose(USER, case_id=CASE, expected_version=7)["routine"]
    assert routine["opportunity_type"] == "DEADLINE"
    assert routine["mode"] == "PREPARE_ONLY"
    assert routine["title"] == "Prepare new deadline mail"
    assert routine["description"] == (
        "Prepare tasks from new deadline mail from this sender. External actions need separate approval."
    )
    assert get(routine_env, f"CASE#{CASE}", "PLAN#000001") == original


def test_proposal_is_inactive_idempotent_and_has_no_private_or_old_event_fields(
    routine_env,
):
    env = routine_env
    seed_source(env)
    result = env.api.propose(USER, case_id=CASE, expected_version=7)
    assert env.api.propose(USER, case_id=CASE, expected_version=7) == result
    routine = result["routine"]
    assert set(routine) == {
        "routine_id",
        "version",
        "status",
        "effective_status",
        "source_case_id",
        "title",
        "description",
        "sender_domain",
        "opportunity_type",
        "mode",
        "activated_at",
        "created_at",
        "updated_at",
        "review_reason",
    }
    assert routine["status"] == routine["effective_status"] == "PROPOSED"
    assert routine["version"] == 1 and routine["activated_at"] is None
    assert (
        routine["mode"] == "PREPARE_ONLY" and routine["opportunity_type"] == "DEADLINE"
    )
    assert routine["sender_domain"] == "sender.example.invalid"
    assert env.api.list(USER) == {"routines": [routine]}
    encoded = json.dumps(result)
    assert ACCOUNT not in encoded and EPOCH not in encoded
    assert all(
        field not in encoded
        for field in (
            "parameters",
            "access_token",
            "source_evidence_ref",
            "ORIGINAL EVENT TITLE",
            "2030-02-01",
        )
    )
    stored = [
        row
        for row in rows(env, f"USER#{USER}")
        if row["SK"]["S"].startswith("ROUTINE#")
    ]
    assert len(stored) == 1 and "ORIGINAL EVENT TITLE" not in json.dumps(stored)


def test_non_marker_calendar_interval_is_appointment_only(routine_env):
    env = routine_env
    seed_source(env, deadline=False)
    assert (
        env.api.propose(USER, case_id=CASE, expected_version=7)["routine"][
            "opportunity_type"
        ]
        == "APPOINTMENT"
    )


@pytest.mark.parametrize(
    "change",
    [
        "case_status",
        "unverified",
        "execution_status",
        "result_ref",
        "source_kind",
        "duplicate_domain",
        "invalid_domain",
        "plan_hash",
        "approval_account",
        "approval_missing",
        "source_owner",
        "revision",
    ],
)
def test_unverified_or_ambiguous_source_cannot_propose(routine_env, change):
    env = routine_env
    seed_source(env)
    sk = (
        "META"
        if change == "case_status"
        else "ACTION#operation-original"
        if change in {"unverified", "execution_status", "result_ref"}
        else "PLAN#000001"
        if change in {"plan_hash", "revision"}
        else "APPROVAL#approval-original"
        if change in {"approval_account", "approval_missing"}
        else "EVIDENCE#source"
    )
    item = get(env, f"CASE#{CASE}", sk)
    if change == "case_status":
        item["status"] = {"S": "QUEUED"}
    elif change == "unverified":
        item["verified"] = {"BOOL": False}
    elif change == "execution_status":
        item["status"] = {"S": "VERIFYING"}
    elif change == "result_ref":
        item["result_ref"] = {"S": "google-calendar:primary:other"}
    elif change == "source_kind":
        item["source"] = {"S": "direct"}
    elif change == "source_owner":
        item["user_id"] = {"S": "other-owner"}
    elif change == "duplicate_domain":
        item["facts"]["L"].append({"S": "sender_domain=sender.example.invalid"})
    elif change == "invalid_domain":
        item["facts"]["L"][0] = {"S": "sender_domain=person@example.invalid"}
    elif change == "approval_account":
        item["account_hash"] = {"S": "b" * 64}
    elif change == "approval_missing":
        item["decision"] = {"S": "REJECT"}
    else:
        plan = json.loads(item["plan_json"]["S"])
        if change == "plan_hash":
            plan["actions"][0]["parameters"]["start"] = "2030-02-01T00:00:00Z"
        else:
            plan["evidence_revisions"] = {
                ref: True for ref in plan["evidence_revisions"]
            }
        item["plan_json"] = j(plan)
    put(env, item)
    with pytest.raises((RoutineNotReady, RoutineConflict)):
        env.api.propose(USER, case_id=CASE, expected_version=7)
    assert env.api.list(USER)["routines"] == []


def test_source_and_routine_owner_and_version_are_checked(routine_env):
    env = routine_env
    seed_source(env)
    with pytest.raises(RoutineNotFound):
        env.api.propose("other", case_id=CASE, expected_version=7)
    with pytest.raises(RoutineConflict):
        env.api.propose(USER, case_id=CASE, expected_version=6)
    with pytest.raises(RoutineInputError):
        env.api.propose(USER, case_id=CASE, expected_version=True)
    proposal = env.api.propose(USER, case_id=CASE, expected_version=7)["routine"]
    with pytest.raises(RoutineNotFound):
        env.api.activate("other", routine_id=proposal["routine_id"], expected_version=1)
    with pytest.raises(RoutineNotFound):
        env.api.pause("other", routine_id=proposal["routine_id"], expected_version=1)
    active = env.api.activate(
        USER, routine_id=proposal["routine_id"], expected_version=1
    )["routine"]
    assert active["version"] == 2 and active["activated_at"]
    with pytest.raises(RoutineConflict):
        env.api.pause(USER, routine_id=active["routine_id"], expected_version=1)
    paused = env.api.pause(USER, routine_id=active["routine_id"], expected_version=2)[
        "routine"
    ]
    assert paused["status"] == paused["effective_status"] == "PAUSED"
    env.now += timedelta(minutes=2)
    again = env.api.activate(USER, routine_id=paused["routine_id"], expected_version=3)[
        "routine"
    ]
    assert again["version"] == 4 and again["activated_at"] != active["activated_at"]


@pytest.mark.parametrize(
    "change", ["connection", "account", "disconnected", "scope", "profile", "source"]
)
def test_changed_bindings_surface_review_required_and_block_activation_but_not_pause(
    routine_env, change
):
    env = routine_env
    seed_source(env)
    routine = activate(env)
    if change == "profile":
        item = get(env, f"USER#{USER}", "MAIL_INTERESTS#google")
        profile = json.loads(item["profile_json"]["S"])
        profile["version"] = 2
        item.update(profile_version={"N": "2"}, profile_json=j(profile))
    elif change == "source":
        item = get(env, f"CASE#{CASE}", "META")
        item["version"] = {"N": "8"}
    else:
        item = get(env, f"USER#{USER}", "CONNECTION#google")
        field, value = {
            "connection": ("mail_connection_id", {"S": "new-epoch"}),
            "account": ("account_hash", {"S": "c" * 64}),
            "disconnected": ("status", {"S": "DISCONNECTED"}),
            "scope": ("granted_scopes", {"L": []}),
        }[change]
        item[field] = value
    put(env, item)
    displayed = env.api.list(USER)["routines"][0]
    assert (
        displayed["status"] == "ACTIVE"
        and displayed["effective_status"] == "REVIEW_REQUIRED"
        and displayed["review_reason"]
    )
    with pytest.raises((RoutineNotReady, RoutineConflict)):
        env.api.activate(USER, routine_id=routine["routine_id"], expected_version=2)
    assert (
        env.api.pause(USER, routine_id=routine["routine_id"], expected_version=2)[
            "routine"
        ]["status"]
        == "PAUSED"
    )


def test_activation_cas_does_not_overwrite_a_concurrent_profile_change(
    routine_env, monkeypatch
):
    env = routine_env
    seed_source(env)
    routine = env.api.propose(USER, case_id=CASE, expected_version=7)["routine"]
    transact, changed = env.db.transact_write_items, []

    def race(**kwargs):
        if not changed:
            changed.append(True)
            item = get(env, f"USER#{USER}", "MAIL_INTERESTS#google")
            profile = json.loads(item["profile_json"]["S"])
            profile["version"] = 2
            item.update(profile_version={"N": "2"}, profile_json=j(profile))
            put(env, item)
        return transact(**kwargs)

    monkeypatch.setattr(env.db, "transact_write_items", race)
    with pytest.raises(RoutineConflict):
        env.api.activate(USER, routine_id=routine["routine_id"], expected_version=1)
    assert env.api.list(USER)["routines"][0]["status"] == "PROPOSED"


def test_new_profile_gets_a_new_inactive_proposal_instead_of_reusing_blocked_rule(
    routine_env,
):
    env = routine_env
    seed_source(env)
    old = activate(env)
    item = get(env, f"USER#{USER}", "MAIL_INTERESTS#google")
    profile = json.loads(item["profile_json"]["S"])
    profile["version"] = 2
    item.update(profile_version={"N": "2"}, profile_json=j(profile))
    put(env, item)
    new = env.api.propose(USER, case_id=CASE, expected_version=7)["routine"]
    assert new["routine_id"] != old["routine_id"] and new["status"] == "PROPOSED"


def test_routine_list_overflow_is_explicit(routine_env):
    env = routine_env
    for index in range(33):
        put(
            env,
            {
                **key(f"USER#{USER}", f"ROUTINE#item-{index:03}"),
                "user_id": {"S": USER},
                "mode": {"S": "PREPARE_ONLY"},
            },
        )
    with pytest.raises(RoutineLimitError):
        env.api.list(USER)


def test_concurrent_proposals_cannot_overflow_the_bounded_routine_list(
    routine_env, monkeypatch
):
    env = routine_env
    seed_source(env)
    first = env.api.propose(USER, case_id=CASE, expected_version=7)["routine"]
    template = get(env, f"USER#{USER}", f"ROUTINE#{first['routine_id']}")
    for index in range(30):
        routine_id = f"existing-routine-{index:02}"
        put(
            env,
            {
                **template,
                "SK": {"S": f"ROUTINE#{routine_id}"},
                "routine_id": {"S": routine_id},
            },
        )
    put(
        env,
        {
            **key(f"USER#{USER}", "ROUTINE_LIMITS"),
            "user_id": {"S": USER},
            "routine_count": {"N": "31"},
        },
    )
    seed_source(env, case_id="capacity-a")
    seed_source(env, case_id="capacity-b")
    transact, raced = env.db.transact_write_items, []

    def interleave(**kwargs):
        if not raced:
            raced.append(True)
            env.api.propose(USER, case_id="capacity-b", expected_version=7)
        return transact(**kwargs)

    monkeypatch.setattr(env.db, "transact_write_items", interleave)
    with pytest.raises(RoutineLimitError):
        env.api.propose(USER, case_id="capacity-a", expected_version=7)
    assert len(env.api.list(USER)["routines"]) == 32
    env.api.propose(USER, case_id="capacity-b", expected_version=7)
    assert get(env, f"USER#{USER}", "ROUTINE_LIMITS")["routine_count"] == {"N": "32"}
