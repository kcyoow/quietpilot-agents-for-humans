"""Run with uv run --with 'moto[dynamodb,sqs]' pytest to verify real expressions."""

from __future__ import annotations

import copy
import hashlib
import json

import boto3
import pytest
from quietpilot_control_api.mail import DynamoMailStore, empty_state
from quietpilot_control_api.suggestions import DynamoSuggestionStore
from quietpilot_control_api.workspace import DynamoWorkspaceStore, WorkspaceConflict
from quietpilot_worker.google_connection_store import (
    CandidateRefreshConflict,
    DynamoConnectionWriter,
    HistorySyncClaim,
    _candidate_fingerprint,
)
from quietpilot_worker.google_jobs import _scan_page_details, _sync_details
from quietpilot_worker.mail_jobs import DynamoMailJobStore, StaleMailJob

moto = pytest.importorskip("moto", reason="Requires the ephemeral Moto test dependency")
TABLE = "candidate-refresh"
OWNER = "synthetic-owner"
EPOCH = "connection-one"
REF = "gmail:" + hashlib.sha256(b"synthetic-message").hexdigest()
SCAN = "a" * 32


def _candidate(*, changed=False, identity="event-one", reference=REF):
    verb = "prepare_reply" if changed else "prepare_task"
    return {
        "outcome": "회신 초안 준비" if changed else "신청 자료 준비",
        "summary": "수정된 요청을 정리해요." if changed else "처음 요청을 정리해요.",
        "why_now": "새 요청이 도착했어요." if changed else "신청할 자료가 있어요.",
        "opportunity_type": "FOLLOW_UP" if changed else "DEADLINE",
        "evidence_refs": [reference],
        "confidence": 0.94 if changed else 0.8,
        "uncertainty_reason": None,
        "primary_group_hint": "follow-ups" if changed else "deadlines",
        "tags": ["follow_up"] if changed else ["deadline"],
        "risk": "MEDIUM" if changed else "LOW",
        "required_capabilities": [
            "quietpilot.reply.prepare" if changed else "quietpilot.task.prepare"
        ],
        "proposed_actions": [
            {
                "connector": "quietpilot",
                "verb": verb,
                "target_resource": "case:updated" if changed else "case:original",
                "parameters": {
                    "source_ref": reference,
                    "title": "회신 초안" if changed else "신청 준비",
                },
                "required_scopes": [],
                "risk": "MEDIUM" if changed else "LOW",
                "reversible": True,
                "verification_method": "case_plan_readback",
            }
        ],
        # The storage contract refreshes one identity; identity generation is tested elsewhere.
        "fingerprint_inputs": [identity],
    }


def _key(candidate, owner=OWNER):
    return {
        "PK": {"S": f"USER#{owner}"},
        "SK": {"S": f"CANDIDATE#{_candidate_fingerprint(candidate)[:32]}"},
    }


def _item(client, key):
    return client.get_item(TableName=TABLE, Key=key, ConsistentRead=True).get("Item")


def _seed(client, owner=OWNER):
    state = empty_state()
    state["profile"].update(tags=["학교"], version=1)
    state["scan"].update(status="PENDING", scan_id=SCAN, profile_version=1)
    client.put_item(
        TableName=TABLE,
        Item={
            "PK": {"S": f"USER#{owner}"},
            "SK": {"S": "CONNECTION#google"},
            "status": {"S": "CONNECTED"},
            "mail_connection_id": {"S": EPOCH},
            "gmail_history_id": {"S": "100"},
        },
    )
    client.put_item(
        TableName=TABLE,
        Item={
            "PK": {"S": f"USER#{owner}"},
            "SK": {"S": "MAIL_INTERESTS#google"},
            "profile_version": {"N": "1"},
            "scan_id": {"S": SCAN},
            "scan_connection_id": {"S": EPOCH},
            "scan_step": {"N": "0"},
            **{
                f"{name}_json": {"S": json.dumps(value)}
                for name, value in state.items()
            },
        },
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("PK", "SK", "GSI1PK", "GSI1SK")
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
        _seed(client)
        yield client


def _persist(client, candidates, *, owner=OWNER, snapshot=None, history=False):
    store = DynamoMailJobStore(TABLE, client)
    state, item, connection = snapshot or store.load(owner)
    references = list(
        dict.fromkeys(
            ref for candidate in candidates for ref in candidate["evidence_refs"]
        )
    )
    evidence = [
        {
            "ref": ref,
            "revision": 1,
            "source": "gmail",
            "title": "학교 안내",
            "facts": ["sender_domain=school.example"],
            "untrusted_text": None,
        }
        for ref in references
    ]
    result = {
        "status": "SYNCED" if history else "SCAN_PAGE",
        "processed_message_count": len(evidence),
        "recent_message_estimate": len(evidence),
        "next_page_token": None,
        "completion_history_id": "100",
        "evidence": evidence,
        "candidates": candidates,
        "discovery_validated": True,
        "unresolved_evidence_count": 0,
        "interest_validated": True,
        "interest_profile_revision": state["profile"]["version"],
        "interest_matches": [
            {
                "evidence_ref": ref,
                "title": "학교 안내",
                "summary": "학교 소식을 확인해요.",
                "reason": "관심 있는 학교 소식이에요.",
                "sender_domain": "school.example",
                "received_at": None,
                "matched_tags": ["학교"],
            }
            for ref in references
        ],
    }
    claim = None
    if history:
        start = connection["gmail_history_id"]["S"]
        claim = HistorySyncClaim(start_history_id=start, token="synthetic-lease")
        client.update_item(
            TableName=TABLE,
            Key={"PK": {"S": f"USER#{owner}"}, "SK": {"S": "CONNECTION#google"}},
            UpdateExpression="SET gmail_sync_token=:token",
            ExpressionAttributeValues={":token": {"S": claim.token}},
        )
        result.update(
            history_id=str(int(start) + 1),
            recovery_mode="INCREMENTAL",
            continuation_required=False,
        )
        page = _sync_details(result, interest_results=True)
    else:
        page = _scan_page_details(result, interest_results=True)
    store.persist(
        owner,
        state,
        item,
        connection["mail_connection_id"]["S"],
        result,
        page,
        DynamoConnectionWriter(TABLE, client),
        step=int(item["scan_step"]["N"]),
        next_work=None,
        history_claim=claim,
    )


@pytest.mark.parametrize("generation", ["current_scan", "new_scan", "new_profile"])
def test_current_reevaluation_replaces_candidate_payload_and_invalidates_old_version(
    client, generation
):
    original, refreshed = _candidate(), _candidate(changed=True)
    _persist(client, [original])
    before = _item(client, _key(original))
    mail = DynamoMailStore(TABLE, client)
    if generation == "new_scan":
        mail.request(OWNER, "scan")
    elif generation == "new_profile":
        mail.save_profile(OWNER, ["학교"], "새로운 학교 소식", 1)
    _persist(client, [refreshed], history=generation == "current_scan")
    after = _item(client, _key(original))
    for field in ("outcome", "summary", "why_now", "opportunity_type", "risk"):
        assert after[field]["S"] == refreshed[field]
    assert after["confidence"]["N"] == str(refreshed["confidence"])
    assert after["primary_group_id"]["S"] == refreshed["primary_group_hint"]
    assert [item["S"] for item in after["tags"]["L"]] == refreshed["tags"]
    assert [item["S"] for item in after["required_capabilities"]["L"]] == refreshed[
        "required_capabilities"
    ]
    assert (
        json.loads(after["proposed_actions_json"]["S"]) == refreshed["proposed_actions"]
    )
    assert after["created_at"] == before["created_at"]
    assert int(after["version"]["N"]) > int(before["version"]["N"])
    current = mail.read(OWNER)
    assert after["mail_scan_id"]["S"] == current["scan"]["scan_id"]
    assert int(after["mail_profile_version"]["N"]) == current["profile"]["version"]
    assert after["GSI1PK"]["S"].endswith("#VISIBLE#follow-ups")
    with pytest.raises(WorkspaceConflict):
        DynamoWorkspaceStore(TABLE, client).convert_candidates(
            OWNER,
            case_id="case-stale",
            candidate_ids=[after["candidate_id"]["S"]],
            expected_versions=[int(before["version"]["N"])],
            now="2026-09-14T00:00:00Z",
        )
    assert _item(client, {"PK": {"S": "CASE#case-stale"}, "SK": {"S": "META"}}) is None
    DynamoWorkspaceStore(TABLE, client).convert_candidates(
        OWNER,
        case_id="case-current",
        candidate_ids=[after["candidate_id"]["S"]],
        expected_versions=[int(after["version"]["N"])],
        now="2026-09-14T00:00:01Z",
    )
    case = _item(client, {"PK": {"S": "CASE#case-current"}, "SK": {"S": "META"}})
    assert (
        json.loads(case["requested_actions_json"]["S"]) == refreshed["proposed_actions"]
    )


@pytest.mark.parametrize("decision", ["hide", "convert"])
def test_user_decision_during_refresh_cannot_drop_the_mail_page_or_modify_a_case(
    client, monkeypatch, decision
):
    original = _candidate()
    _persist(client, [original])
    before = _item(client, _key(original))
    candidate_id = before["candidate_id"]["S"]
    transact = client.transact_write_items
    decided = []
    case_before = []

    def interleave(**request):
        if not decided and any(
            op.get("Update", {}).get("Key") == _key(original)
            for op in request["TransactItems"]
        ):
            decided.append(True)
            if decision == "hide":
                DynamoSuggestionStore(TABLE, client).hide_once(OWNER, candidate_id, 1)
            else:
                DynamoWorkspaceStore(TABLE, client).convert_candidates(
                    OWNER,
                    case_id="case-active",
                    candidate_ids=[candidate_id],
                    expected_versions=[1],
                    now="2026-09-14T00:00:00Z",
                )
                case_before.extend(
                    client.query(
                        TableName=TABLE,
                        KeyConditionExpression="PK=:pk",
                        ExpressionAttributeValues={":pk": {"S": "CASE#case-active"}},
                    )["Items"]
                )
            decided.append(copy.deepcopy(_item(client, _key(original))))
        return transact(**request)

    monkeypatch.setattr(client, "transact_write_items", interleave)
    _persist(client, [_candidate(changed=True)], history=True)
    assert decided
    assert _item(client, _key(original)) == decided[1]
    state, item, connection = DynamoMailJobStore(TABLE, client).load(OWNER)
    assert state["scan"]["status"] == "READY"
    assert state["scan"]["processed_count"] == 2
    assert item["scan_step"] == {"N": "2"}
    assert connection["gmail_history_id"] == {"S": "101"}
    if decision == "convert":
        case_after = client.query(
            TableName=TABLE,
            KeyConditionExpression="PK=:pk",
            ExpressionAttributeValues={":pk": {"S": "CASE#case-active"}},
        )["Items"]
        assert case_after == case_before


@pytest.mark.parametrize("status", ["HIDDEN", "SELECTED", "CONVERTED", "EXPIRED"])
def test_decided_candidates_are_not_rewritten_or_reindexed_on_a_new_scan(
    client, status
):
    candidate = _candidate()
    _persist(client, [candidate])
    client.update_item(
        TableName=TABLE,
        Key=_key(candidate),
        UpdateExpression="SET #status=:status, version=:version REMOVE GSI1PK,GSI1SK",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": {"S": status}, ":version": {"N": "2"}},
    )
    before = _item(client, _key(candidate))
    DynamoMailStore(TABLE, client).request(OWNER, "scan")
    _persist(client, [_candidate(changed=True)])
    assert _item(client, _key(candidate)) == before
    assert DynamoMailStore(TABLE, client).read(OWNER)["scan"]["status"] == "READY"


@pytest.mark.parametrize("concurrent", [False, True])
def test_refresh_preserves_group_suppression_and_undo_reveals_current_content(
    client, monkeypatch, concurrent
):
    target = _candidate()
    second = _candidate(identity="event-two", reference="gmail:" + "2" * 64)
    anchor = _candidate(identity="event-anchor", reference="gmail:" + "3" * 64)
    _persist(client, [target, second, anchor])
    anchor_id = _candidate_fingerprint(anchor)[:32]
    suggestions = DynamoSuggestionStore(TABLE, client)
    rule = []
    if not concurrent:
        rule.append(suggestions.reduce_similar(OWNER, anchor_id, 1))
    transact = client.transact_write_items
    attempts = []

    def interleave(**request):
        if any(
            op.get("Update", {}).get("Key") == _key(target)
            for op in request["TransactItems"]
        ):
            attempts.append(True)
            if concurrent and not rule:
                rule.append(suggestions.reduce_similar(OWNER, anchor_id, 1))
        return transact(**request)

    monkeypatch.setattr(client, "transact_write_items", interleave)
    fresh = _candidate(changed=True)
    fresh_second = _candidate(
        changed=True, identity="event-two", reference="gmail:" + "2" * 64
    )
    _persist(client, [fresh, fresh_second], history=True)
    assert len(attempts) == (2 if concurrent else 1)
    assert [
        candidate["candidate_id"] for candidate in suggestions.list_visible(OWNER)
    ] == [anchor_id]
    for candidate in (fresh, fresh_second):
        item = _item(client, _key(candidate))
        assert item["primary_group_id"] == {"S": "deadlines"}
        assert item["summary"] == {"S": candidate["summary"]}
        assert (
            json.loads(item["proposed_actions_json"]["S"])
            == candidate["proposed_actions"]
        )
        assert item["version"] == {"N": "2"}
    suggestions.undo_suppression(OWNER, rule[0]["rule_id"])
    visible = {
        candidate["candidate_id"]: candidate
        for candidate in suggestions.list_visible(OWNER)
    }
    assert len(visible) == 3
    assert visible[_candidate_fingerprint(target)[:32]]["summary"] == fresh["summary"]
    assert DynamoMailJobStore(TABLE, client).load(OWNER)[1]["scan_step"] == {"N": "2"}


@pytest.mark.parametrize("generation", ["profile", "scan", "connection", "page"])
def test_superseded_job_cannot_overwrite_fresh_candidate_or_commit_partial_results(
    client, generation
):
    candidate, fresh = _candidate(), _candidate(changed=True)
    _persist(client, [candidate])
    store = DynamoMailJobStore(TABLE, client)
    stale = store.load(OWNER)
    if generation == "profile":
        DynamoMailStore(TABLE, client).save_profile(OWNER, ["학교"], "새 관심사", 1)
    elif generation == "scan":
        DynamoMailStore(TABLE, client).request(OWNER, "scan")
    elif generation == "connection":
        for sk, field in (
            ("CONNECTION#google", "mail_connection_id"),
            ("MAIL_INTERESTS#google", "scan_connection_id"),
        ):
            client.update_item(
                TableName=TABLE,
                Key={"PK": {"S": f"USER#{OWNER}"}, "SK": {"S": sk}},
                UpdateExpression=f"SET {field}=:new",
                ExpressionAttributeValues={":new": {"S": "new-connection"}},
            )
    _persist(client, [fresh], history=generation == "page")
    before = copy.deepcopy(client.scan(TableName=TABLE)["Items"])
    with pytest.raises(StaleMailJob):
        _persist(client, [candidate], snapshot=stale)
    assert client.scan(TableName=TABLE)["Items"] == before


def test_same_fingerprint_is_owner_scoped_and_foreign_identity_is_rejected(client):
    _seed(client, "other-owner")
    original, fresh = _candidate(), _candidate(changed=True)
    _persist(client, [original])
    _persist(client, [original], owner="other-owner")
    other_before = copy.deepcopy(_item(client, _key(original, "other-owner")))
    _persist(client, [fresh], history=True)
    assert _item(client, _key(original, "other-owner")) == other_before
    client.update_item(
        TableName=TABLE,
        Key=_key(original),
        UpdateExpression="SET user_id=:other",
        ExpressionAttributeValues={":other": {"S": "other-owner"}},
    )
    with pytest.raises(ValueError, match="identity"):
        _persist(client, [original])
    assert _item(client, _key(original, "other-owner")) == other_before


def test_continuous_candidate_contention_remains_retryable_without_advancing_the_page(
    client, monkeypatch
):
    original = _candidate()
    _persist(client, [original])
    transact = client.transact_write_items
    attempts = []

    def change_version(**request):
        if any(
            op.get("Update", {}).get("Key") == _key(original)
            for op in request["TransactItems"]
        ):
            attempts.append(True)
            client.update_item(
                TableName=TABLE,
                Key=_key(original),
                UpdateExpression="SET version=version+:one",
                ExpressionAttributeValues={":one": {"N": "1"}},
            )
        return transact(**request)

    monkeypatch.setattr(client, "transact_write_items", change_version)
    with pytest.raises(CandidateRefreshConflict):
        _persist(client, [_candidate(changed=True)], history=True)
    assert len(attempts) == 3
    assert DynamoMailJobStore(TABLE, client).load(OWNER)[1]["scan_step"] == {"N": "1"}
    assert _item(client, _key(original))["outcome"] == {"S": original["outcome"]}
