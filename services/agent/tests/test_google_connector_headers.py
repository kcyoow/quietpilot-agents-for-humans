from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from quietpilot_agent.google_connector import (
    GMAIL_READONLY_SCOPE,
    GoogleConnector,
    _safe_header,
)

MALFORMED_SUBJECT = "=?utf-8?b?A?="
VALID_SUBJECT = "과제 제출 마감"
ENCODED_SUBJECT = f"=?utf-8?b?{base64.b64encode(VALID_SUBJECT.encode()).decode()}?="
INTERNAL_DATE = "1788000000000"


class _Identity:
    def get_resource_oauth2_token(self, **values: object) -> dict[str, str]:
        assert values["scopes"] == [GMAIL_READONLY_SCOPE]
        return {"accessToken": "offline-fixture-token"}


class _Google:
    def __init__(self) -> None:
        self.message_reads: list[str] = []

    def request_json(
        self, method: str, url: str, **values: object
    ) -> dict[str, object]:
        assert method == "GET"
        parsed = urlparse(url)
        if parsed.path.endswith("/history"):
            return {
                "historyId": "15",
                "history": [
                    {
                        "id": "11",
                        "messagesAdded": [{"message": {"id": "good-message"}}],
                    },
                    {"id": "12", "messagesAdded": [{"message": {"id": "bad-message"}}]},
                ],
            }
        if parsed.path.endswith("/messages"):
            return {
                "messages": [{"id": "good-message"}, {"id": "bad-message"}],
                "resultSizeEstimate": 2,
                "nextPageToken": "next-page",
            }
        if "/messages/" in parsed.path:
            message_id = parsed.path.rsplit("/", 1)[1]
            assert message_id in {"good-message", "bad-message"}
            assert parse_qs(parsed.query) == {
                "format": ["full"],
                "fields": ["id,internalDate,snippet,payload"],
            }
            self.message_reads.append(message_id)
            return {
                "id": message_id,
                "internalDate": INTERNAL_DATE,
                "payload": {
                    "headers": [
                        {
                            "name": "Subject",
                            "value": ENCODED_SUBJECT
                            if message_id == "good-message"
                            else MALFORMED_SUBJECT,
                        },
                        {"name": "From", "value": "sender@example.test"},
                    ],
                },
                "snippet": "제출할 서류를 확인해 주세요.",
            }
        raise AssertionError("Unexpected fake Google request")


def _connector() -> tuple[GoogleConnector, _Google]:
    google = _Google()
    return (
        GoogleConnector(
            identity=_Identity(),
            google=google,
            oauth_return_url="https://example.test/oauth-return",
        ),
        google,
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Plain subject", "Plain subject"),
        (ENCODED_SUBJECT, VALID_SUBJECT),
        ("=?utf-8?q?Submission_deadline?=", "Submission deadline"),
    ],
)
def test_normal_plain_and_encoded_subjects_keep_their_decoded_text(
    header: str, expected: str
) -> None:
    assert _safe_header(header, "제목 없음") == expected


def test_malformed_encoded_subject_uses_the_existing_sanitized_raw_fallback() -> None:
    header = MALFORMED_SUBJECT + "\r\n note\x01 " + "x" * 210

    normalized = _safe_header(header, "제목 없음")

    assert normalized == (MALFORMED_SUBJECT + " note " + "x" * 210)[:200]
    assert len(normalized) == 200
    assert all(character.isprintable() for character in normalized)


@pytest.mark.parametrize("operation", ["scan_page", "sync_history"])
def test_malformed_subject_does_not_discard_a_valid_sibling_message(
    operation: str,
) -> None:
    connector, google = _connector()
    if operation == "scan_page":
        result = connector.scan_page(
            workload_access_token="offline-workload", page_token="first-page"
        )
        assert result["status"] == "SCAN_PAGE"
        assert result["processed_message_count"] == 2
        assert result["next_page_token"] == "next-page"
    else:
        result = connector.sync_history(
            workload_access_token="offline-workload", start_history_id="10"
        )
        assert result["status"] == "SYNCED"
        assert result["history_id"] == "15"
        assert result["recovery_mode"] == "INCREMENTAL"
        assert result["continuation_required"] is False

    assert google.message_reads == ["good-message", "bad-message"]
    evidence = result["evidence"]
    assert isinstance(evidence, list)
    assert [record["title"] for record in evidence] == [
        VALID_SUBJECT,
        MALFORMED_SUBJECT,
    ]
    assert [record["ref"] for record in evidence] == [
        f"gmail:{hashlib.sha256(message_id.encode()).hexdigest()}"
        for message_id in ["good-message", "bad-message"]
    ]
    assert all(
        record["facts"]
        == [
            f"received_at_unix_ms={INTERNAL_DATE}",
            "sender_domain=example.test",
            "source_content=snippet_only",
        ]
        and record["untrusted_text"] == "제출할 서류를 확인해 주세요."
        for record in evidence
    )
