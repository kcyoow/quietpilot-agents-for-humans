from __future__ import annotations

import base64
import copy
import hashlib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from quietpilot_agent.google_connector import (
    CASE_EVIDENCE_PAGE_SIZE,
    GMAIL_READONLY_SCOPE,
    GoogleApiError,
    GoogleAuthorizationRequired,
    GoogleCaseEvidenceUnavailable,
    GoogleConnector,
)
from quietpilot_agent.models import EvidenceRecord

ACCOUNT = "owner@example.invalid"
ACCOUNT_HASH = hashlib.sha256(ACCOUNT.encode()).hexdigest()
RECEIVED = str(int(datetime(2026, 9, 14, tzinfo=UTC).timestamp() * 1000))


def ref(message_id: str) -> str:
    return "gmail:" + hashlib.sha256(message_id.encode("ascii")).hexdigest()


def evidence(
    message_id: str, *, received: str = RECEIVED, revision: int = 7
) -> dict[str, object]:
    return {
        "user_id": "case-owner",
        "ref": ref(message_id),
        "revision": revision,
        "source": "gmail",
        "title": "예약 일정 안내",
        "facts": [f"received_at_unix_ms={received}", "sender_domain=example.invalid"],
        "untrusted_text": None,
    }


def message(message_id: str, *, received: str = RECEIVED) -> dict[str, object]:
    text = "예약 일정은 9월 20일 오후 3시입니다. 인증번호: 123456"
    return {
        "id": message_id,
        "internalDate": received,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "예약 일정 안내"},
                {"name": "From", "value": "service@example.invalid"},
            ],
            "body": {
                "data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
            },
        },
    }


class Identity:
    def __init__(self) -> None:
        self.calls = 0

    def get_resource_oauth2_token(self, **values: object) -> dict[str, str]:
        self.calls += 1
        assert values["workloadIdentityToken"] == "synthetic-workload-token"
        assert values["scopes"] == [GMAIL_READONLY_SCOPE]
        return {"accessToken": "synthetic-access-token"}


class ScriptedGoogle:
    def __init__(self, *steps: tuple[str, object]) -> None:
        self.steps = list(steps)
        self.calls: list[tuple[str, dict[str, list[str]]]] = []

    def request_json(self, method: str, url: str, **values: object) -> Any:
        assert method == "GET"
        assert values == {"access_token": "synthetic-access-token"}
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc == "gmail.googleapis.com"
        query = parse_qs(parsed.query)
        prefix = "/gmail/v1/users/me/"
        assert parsed.path.startswith(prefix)
        endpoint = parsed.path.removeprefix(prefix)
        if endpoint == "profile":
            assert query == {"fields": ["emailAddress"]}
        elif endpoint == "messages":
            assert set(query) in (
                {"q", "maxResults", "fields"},
                {"q", "maxResults", "fields", "pageToken"},
            )
            assert query["fields"] == ["messages/id,nextPageToken"]
            assert query["maxResults"] == ["250"]
            assert "labelIds" not in query and "includeSpamTrash" not in query
        else:
            assert endpoint.startswith("messages/")
            assert query == {
                "format": ["full"],
                "fields": ["id,internalDate,snippet,payload"],
            }
        self.calls.append((endpoint, query))
        expected, response = self.steps.pop(0)
        assert endpoint == expected
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


def connector(*steps: tuple[str, object]):
    identity, http = Identity(), ScriptedGoogle(*steps)
    return (
        GoogleConnector(
            identity, http, oauth_return_url="https://example.invalid/oauth"
        ),
        identity,
        http,
    )


def resolve(
    client: GoogleConnector, records: list[dict[str, object]], **overrides: object
) -> list[dict[str, object]]:
    return client.resolve_case_evidence(
        **{
            "workload_access_token": "synthetic-workload-token",
            "account_hash": ACCOUNT_HASH,
            "evidence": records,
            **overrides,
        }
    )


def test_exact_hash_reads_only_the_requested_archived_mail_and_preserves_owner_revision() -> (
    None
):
    original = evidence("wanted-message")
    saved = copy.deepcopy(original)
    client, identity, http = connector(
        ("profile", {"emailAddress": ACCOUNT.upper()}),
        (
            "messages",
            {"messages": [{"id": "unrelated-message"}, {"id": "wanted-message"}]},
        ),
        ("messages/wanted-message", message("wanted-message")),
    )
    result = resolve(client, [original])
    assert len(result) == 1 and original == saved
    assert result[0]["user_id"] == original["user_id"]
    assert result[0]["ref"] == original["ref"]
    assert result[0]["revision"] == 7
    assert result[0]["title"] == original["title"]
    assert "source_content=body" in result[0]["facts"]
    assert "123456" not in result[0]["untrusted_text"]
    assert "오후 3시" in result[0]["untrusted_text"]
    assert "wanted-message" not in str(result) and "synthetic-access-token" not in str(
        result
    )
    EvidenceRecord.model_validate(result[0])
    day_start = int(RECEIVED) // 1000
    assert http.calls[1][1]["q"] == [
        f"after:{day_start - 1} before:{day_start + 86400}"
    ]
    assert identity.calls == 1 and not http.steps


def test_every_requested_id_is_resolved_before_any_body_and_input_order_is_preserved() -> (
    None
):
    next_day = str(int(RECEIVED) + 86_400_000)
    records = [evidence("tomorrow", received=next_day), evidence("today")]
    client, _, http = connector(
        ("profile", {"emailAddress": ACCOUNT}),
        ("messages", {"messages": [{"id": "tomorrow"}]}),
        ("messages", {"messages": [{"id": "today"}]}),
        ("messages/tomorrow", message("tomorrow", received=next_day)),
        ("messages/today", message("today")),
    )
    result = resolve(client, records)
    assert [record["ref"] for record in result] == [record["ref"] for record in records]
    assert [endpoint for endpoint, _ in http.calls] == [
        "profile",
        "messages",
        "messages",
        "messages/tomorrow",
        "messages/today",
    ]
    today = int(RECEIVED) // 1000
    assert http.calls[1][1]["q"] == [
        f"after:{today + 86400 - 1} before:{today + 172800}"
    ]


def test_selected_case_reads_beyond_scan_limit_without_losing_tail_or_redaction():
    body = "Service policy details. " * 250 + "No action is required. 인증번호: 123456"
    source = message("long-message")
    source["payload"]["body"]["data"] = base64.urlsafe_b64encode(body.encode()).decode()
    client, _, http = connector(
        ("profile", {"emailAddress": ACCOUNT}),
        ("messages", {"messages": [{"id": "long-message"}]}),
        ("messages/long-message", source),
        ("messages/long-message", source),
    )
    current = resolve(client, [evidence("long-message")])[0]
    assert len(current["untrusted_text"]) > 4000
    assert "No action is required." in current["untrusted_text"]
    assert "123456" not in current["untrusted_text"]
    assert "source_truncated=true" not in current["facts"]
    scanned = client._message_evidence(
        access_token="synthetic-access-token", message_id="long-message"
    )
    assert len(scanned["untrusted_text"]) == 4000
    assert "source_truncated=true" in scanned["facts"]
    assert not http.steps


def test_two_pages_share_one_day_query_and_never_fetch_unrelated_bodies() -> None:
    first_page = [
        {"id": f"unrelated-{index}"} for index in range(CASE_EVIDENCE_PAGE_SIZE - 1)
    ] + [{"id": "first"}]
    second_page = [
        {"id": f"other-{index}"} for index in range(CASE_EVIDENCE_PAGE_SIZE - 1)
    ] + [{"id": "second"}]
    client, _, http = connector(
        ("profile", {"emailAddress": ACCOUNT}),
        ("messages", {"messages": first_page, "nextPageToken": "page-two"}),
        ("messages", {"messages": second_page}),
        ("messages/second", message("second")),
        ("messages/first", message("first")),
    )
    result = resolve(client, [evidence("second"), evidence("first")])
    assert len(result) == 2
    assert http.calls[1][1]["q"] == http.calls[2][1]["q"]
    assert http.calls[2][1]["pageToken"] == ["page-two"]
    assert not http.steps


def test_missing_ref_in_another_day_does_not_even_read_the_already_resolved_body() -> (
    None
):
    client, _, http = connector(
        ("profile", {"emailAddress": ACCOUNT}),
        ("messages", {"messages": [{"id": "known"}]}),
        ("messages", {}),
    )
    with pytest.raises(GoogleCaseEvidenceUnavailable) as caught:
        resolve(
            client,
            [
                evidence("known"),
                evidence("missing", received=str(int(RECEIVED) + 86_400_000)),
            ],
        )
    assert caught.value.code == "NOT_FOUND"
    assert [endpoint for endpoint, _ in http.calls] == [
        "profile",
        "messages",
        "messages",
    ]


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (
            {"messages": [{"id": "noise-one"}], "nextPageToken": "same-token"},
            {"messages": [{"id": "noise-two"}], "nextPageToken": "same-token"},
            "REPEATED_PAGE",
        ),
        (
            {"messages": [{"id": "noise-one"}], "nextPageToken": "one"},
            {"messages": [{"id": "noise-one"}], "nextPageToken": "two"},
            "REPEATED_PAGE",
        ),
        (
            {"messages": [{"id": "noise-one"}], "nextPageToken": "one"},
            {"messages": [{"id": "noise-two"}], "nextPageToken": "two"},
            "SEARCH_LIMIT",
        ),
    ],
)
def test_repeated_pages_cursors_and_search_limit_stop_without_body_reads(
    first: dict[str, object], second: dict[str, object], expected: str
) -> None:
    client, _, http = connector(
        ("profile", {"emailAddress": ACCOUNT}),
        ("messages", first),
        ("messages", second),
    )
    with pytest.raises(GoogleCaseEvidenceUnavailable) as caught:
        resolve(client, [evidence("missing")])
    assert caught.value.code == expected
    assert len(http.calls) == 3


@pytest.mark.parametrize(
    "response",
    [
        {"messages": [{"id": "noise"}] * 251},
        {"messages": [{"id": "../bad"}]},
        {"messages": "invalid"},
        {"nextPageToken": ""},
        {"nextPageToken": "private cursor\nvalue"},
    ],
)
def test_invalid_or_oversized_metadata_cannot_expand_the_read_scope(
    response: dict[str, object],
) -> None:
    client, _, http = connector(
        ("profile", {"emailAddress": ACCOUNT}), ("messages", response)
    )
    with pytest.raises(GoogleCaseEvidenceUnavailable):
        resolve(client, [evidence("wanted")])
    assert len(http.calls) == 2


@pytest.mark.parametrize(
    "boundary",
    [
        "account",
        "title",
        "received",
        "identity",
        "body_missing",
        "deleted",
        "provider_error",
    ],
)
def test_changed_or_unavailable_source_never_returns_partial_repaired_evidence(
    boundary: str,
) -> None:
    body: object = message("wanted")
    if boundary == "title":
        body["payload"]["headers"][0]["value"] = "Changed source title"
    elif boundary == "received":
        body["internalDate"] = str(int(RECEIVED) + 1)
    elif boundary == "identity":
        body["id"] = "another-message"
    elif boundary == "body_missing":
        body["payload"]["body"] = {}
        body["snippet"] = "A snippet is insufficient for the exact original body."
    elif boundary == "deleted":
        body = GoogleApiError(404)
    elif boundary == "provider_error":
        body = RuntimeError("private original body and raw message id must not escape")
    steps = [
        (
            "profile",
            {
                "emailAddress": "another@example.invalid"
                if boundary == "account"
                else ACCOUNT
            },
        )
    ]
    if boundary != "account":
        steps.extend(
            [("messages", {"messages": [{"id": "wanted"}]}), ("messages/wanted", body)]
        )
    client, _, http = connector(*steps)
    with pytest.raises(GoogleCaseEvidenceUnavailable) as caught:
        resolve(client, [evidence("wanted")])
    expected = {
        "account": "ACCOUNT_CHANGED",
        "title": "SOURCE_CHANGED",
        "received": "SOURCE_CHANGED",
        "identity": "READ_FAILED",
        "body_missing": "SOURCE_UNAVAILABLE",
        "deleted": "NOT_FOUND",
        "provider_error": "READ_FAILED",
    }
    assert caught.value.code == expected[boundary]
    assert "private original" not in str(caught.value) and "wanted" not in str(
        caught.value
    )
    assert caught.value.__cause__ is None
    assert not http.steps


def test_second_deleted_body_does_not_return_first_success_as_a_fallback() -> None:
    client, _, _ = connector(
        ("profile", {"emailAddress": ACCOUNT}),
        ("messages", {"messages": [{"id": "first"}, {"id": "second"}]}),
        ("messages/first", message("first")),
        ("messages/second", GoogleApiError(404)),
    )
    with pytest.raises(GoogleCaseEvidenceUnavailable, match="NOT_FOUND"):
        resolve(client, [evidence("first"), evidence("second")])


@pytest.mark.parametrize(
    "change",
    [
        "zero",
        "nine",
        "duplicate",
        "owner",
        "boolean_revision",
        "zero_revision",
        "bad_ref",
        "missing_time",
        "duplicate_time",
        "overflow_time",
        "non_gmail",
        "bad_account",
    ],
)
def test_invalid_case_binding_fails_before_identity_or_gmail(change: str) -> None:
    records = [evidence("wanted")]
    account = ACCOUNT_HASH
    if change == "zero":
        records = []
    elif change == "nine":
        records = [evidence(str(index)) for index in range(9)]
    elif change == "duplicate":
        records *= 2
    elif change == "owner":
        records.append({**evidence("another"), "user_id": "another-owner"})
    elif change == "boolean_revision":
        records[0]["revision"] = True
    elif change == "zero_revision":
        records[0]["revision"] = 0
    elif change == "bad_ref":
        records[0]["ref"] = "gmail:raw-message-id"
    elif change == "missing_time":
        records[0]["facts"] = []
    elif change == "duplicate_time":
        records[0]["facts"] = [f"received_at_unix_ms={RECEIVED}"] * 2
    elif change == "overflow_time":
        records[0]["facts"] = ["received_at_unix_ms=999999999999999"]
    elif change == "non_gmail":
        records[0]["source"] = "direct"
    else:
        account = "invalid hash"
    client, identity, http = connector()
    with pytest.raises(GoogleCaseEvidenceUnavailable, match="INVALID_RECORD"):
        resolve(client, records, account_hash=account)
    assert identity.calls == 0 and http.calls == []


def test_unauthorized_gmail_read_preserves_the_existing_reconnection_boundary() -> None:
    client, _, http = connector(("profile", GoogleApiError(401)))
    with pytest.raises(GoogleAuthorizationRequired):
        resolve(client, [evidence("wanted")])
    assert len(http.calls) == 1
