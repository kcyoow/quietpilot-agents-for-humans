from __future__ import annotations

import copy
import json
import re
import urllib.error
from collections.abc import Callable, Mapping
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit

import pytest
from quietpilot_agent.google_calendar import (
    GoogleCalendarExecutor,
    normalize_calendar_parameters,
)
from quietpilot_agent.google_connector import GoogleApiError, UrlLibGoogleHttpClient

PARAMETERS = {
    "summary": "장학금 제출 확인",
    "start": "2026-09-20T09:00:00+09:00",
    "end": "2026-09-20T09:30:00+09:00",
    "description": "승인한 서류를 확인한다.\n제출은 별도 작업이다.",
    "timeZone": "Asia/Seoul",
}
LINK = "https://www.google.com/calendar/event?eid=offline-event"
Response = (
    Mapping[str, Any] | Exception | Callable[["ScriptedGoogle"], Mapping[str, Any]]
)


class ScriptedGoogle:
    def __init__(self, *steps: tuple[str, Response]) -> None:
        self.steps = list(steps)
        self.calls: list[tuple[str, str, Mapping[str, object] | None]] = []
        self.created: dict[str, Any] | None = None

    def request_json(
        self,
        method: str,
        url: str,
        *,
        access_token: str,
        body: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        assert access_token == "synthetic-access-token"
        parsed = urlsplit(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "www.googleapis.com"
        assert parsed.path.startswith("/calendar/v3/calendars/primary/events")
        assert "synthetic-access-token" not in url
        self.calls.append((method, url, copy.deepcopy(body)))
        expected_method, response = self.steps.pop(0)
        assert method == expected_method
        if method == "POST":
            assert body is not None
            self.created = copy.deepcopy(dict(body))
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(self)
        return copy.deepcopy(response)

    def revoke(self, access_token: str) -> None:
        pytest.fail("Calendar execution must not revoke a token")


def created(google: ScriptedGoogle) -> Mapping[str, Any]:
    assert google.created is not None
    return {**copy.deepcopy(google.created), "htmlLink": LINK}


def execute(
    google: ScriptedGoogle,
    *,
    parameters: object = None,
    operation_id: str = "owner:action:1",
) -> dict[str, Any]:
    return GoogleCalendarExecutor(google).execute(
        access_token="synthetic-access-token",
        operation_id=operation_id,
        parameters=PARAMETERS if parameters is None else parameters,  # type: ignore[arg-type]
    )


def test_insert_uses_only_approved_fields_and_completes_after_separate_get() -> None:
    google = ScriptedGoogle(
        ("GET", GoogleApiError(404)), ("POST", {}), ("GET", created)
    )
    result = execute(google)
    assert result == {
        "status": "COMPLETED",
        "verified": True,
        "error_code": None,
        "result_ref": f"google-calendar:primary:{google.created['id']}",
        "html_url": LINK,
    }
    assert [call[0] for call in google.calls] == ["GET", "POST", "GET"]
    assert google.calls[0][1] == google.calls[2][1]
    body = google.created
    assert body is not None
    assert re.fullmatch(r"[0-9a-v]{5,1024}", body["id"])
    assert "owner:action:1" not in body["id"]
    assert body["summary"] == PARAMETERS["summary"]
    assert body["description"] == PARAMETERS["description"]
    assert body["start"] == {"dateTime": PARAMETERS["start"], "timeZone": "Asia/Seoul"}
    assert body["end"] == {"dateTime": PARAMETERS["end"], "timeZone": "Asia/Seoul"}
    assert body["visibility"] == "private"
    assert body["reminders"] == {"useDefault": False}
    assert body["eventType"] == "default"
    assert body["status"] == "confirmed"
    assert set(body) == {
        "id",
        "summary",
        "description",
        "start",
        "end",
        "eventType",
        "status",
        "visibility",
        "reminders",
        "extendedProperties",
    }
    assert parse_qs(urlsplit(google.calls[1][1]).query) == {"sendUpdates": ["none"]}
    assert set(body["extendedProperties"]) == {"private"}
    assert "synthetic-access-token" not in json.dumps(result)
    assert "owner:action:1" not in json.dumps(body)
    assert not google.steps


def test_replayed_operation_reads_existing_event_without_another_insert() -> None:
    first = ScriptedGoogle(("GET", GoogleApiError(404)), ("POST", {}), ("GET", created))
    expected = execute(first)
    replay = ScriptedGoogle(("GET", created(first)))
    assert execute(replay) == expected
    assert len(replay.calls) == 1


@pytest.mark.parametrize(
    "failure",
    [
        GoogleApiError(409),
        GoogleApiError(408),
        GoogleApiError(429),
        GoogleApiError(503),
        TimeoutError("secret response must not escape"),
        RuntimeError("transport failed"),
        ValueError("malformed response after write"),
    ],
)
def test_unknown_or_conflicted_insert_is_resolved_by_same_id_readback(
    failure: Exception,
) -> None:
    google = ScriptedGoogle(
        ("GET", GoogleApiError(404)), ("POST", failure), ("GET", created)
    )
    assert execute(google)["verified"] is True
    assert [call[0] for call in google.calls] == ["GET", "POST", "GET"]
    assert google.calls[0][1] == google.calls[2][1]


@pytest.mark.parametrize(
    "read_failure", [GoogleApiError(404), GoogleApiError(503), TimeoutError()]
)
def test_accepted_but_unreadable_event_stays_verifying(read_failure: Exception) -> None:
    google = ScriptedGoogle(
        ("GET", GoogleApiError(404)), ("POST", created), ("GET", read_failure)
    )
    result = execute(google)
    assert result["status"] == "VERIFYING"
    assert result["verified"] is False
    assert result["html_url"] is None
    assert result["result_ref"]
    assert len(google.calls) == 3


@pytest.mark.parametrize(
    "failure",
    [GoogleApiError(500), GoogleApiError(429), TimeoutError(), ValueError("secret")],
)
def test_uncertain_initial_read_never_performs_a_write(failure: Exception) -> None:
    google = ScriptedGoogle(("GET", failure))
    result = execute(google)
    assert result["status"] == "VERIFYING"
    assert result["verified"] is False
    assert len(google.calls) == 1
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "GOOGLE_AUTH_REQUIRED"),
        (403, "GOOGLE_CALENDAR_FORBIDDEN"),
        (400, "GOOGLE_CALENDAR_REQUEST_REJECTED"),
        (410, "CALENDAR_EVENT_GONE"),
    ],
)
@pytest.mark.parametrize("phase", ["before", "insert", "after"])
def test_explicit_failures_never_become_completion_or_blind_retries(
    status: int, code: str, phase: str
) -> None:
    steps: list[tuple[str, Response]] = []
    if phase != "before":
        steps.append(("GET", GoogleApiError(404)))
    if phase == "after":
        steps.append(("POST", {}))
    steps.append(("POST" if phase == "insert" else "GET", GoogleApiError(status)))
    google = ScriptedGoogle(*steps)
    result = execute(google)
    assert result["status"] == "FAILED"
    assert result["error_code"] == code
    assert result["verified"] is False
    assert not google.steps


@pytest.mark.parametrize(
    "changes",
    [
        {"summary": "changed title"},
        {"description": "changed content"},
        {"id": "another-id"},
        {"visibility": "public"},
        {"status": "tentative"},
        {"start": {"dateTime": "2026-09-20T10:00:00+09:00", "timeZone": "Asia/Seoul"}},
        {"end": {"dateTime": "2026-09-20T10:30:00+09:00", "timeZone": "Asia/Seoul"}},
        {"extendedProperties": {}},
        {"extendedProperties": {"private": {"quietpilotOperation": "other"}}},
        {"attendees": [{"email": "unapproved@example.invalid"}]},
        {"attendeesOmitted": True},
        {"reminders": {"useDefault": True}},
        {
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "email", "minutes": 10}],
            }
        },
        {"recurrence": ["RRULE:FREQ=DAILY"]},
        {"conferenceData": {"conferenceId": "extra"}},
        {"attachments": [{"fileUrl": "https://example.invalid/private"}]},
        {"endTimeUnspecified": True},
    ],
)
def test_matching_markers_do_not_override_changed_or_unapproved_readback(
    changes: dict[str, Any],
) -> None:
    first = ScriptedGoogle(("GET", GoogleApiError(404)), ("POST", {}), ("GET", created))
    execute(first)
    replay = ScriptedGoogle(("GET", {**created(first), **changes}))
    result = execute(replay)
    assert result["status"] == "FAILED"
    assert result["error_code"] == "CALENDAR_READBACK_MISMATCH"
    assert len(replay.calls) == 1


def test_same_operation_with_changed_approved_content_cannot_create_another_event() -> (
    None
):
    first = ScriptedGoogle(("GET", GoogleApiError(404)), ("POST", {}), ("GET", created))
    execute(first)
    replay = ScriptedGoogle(("GET", created(first)))
    result = execute(replay, parameters={**PARAMETERS, "summary": "new plan"})
    assert result["status"] == "FAILED"
    assert replay.calls[0][1] == first.calls[0][1]
    assert len(replay.calls) == 1


def test_a_cancelled_tombstone_is_not_recreated() -> None:
    google = ScriptedGoogle(("GET", {"status": "cancelled"}))
    result = execute(google)
    assert result["error_code"] == "CALENDAR_EVENT_CANCELLED"
    assert result["verified"] is False
    assert len(google.calls) == 1


def test_all_day_range_preserves_exclusive_end_without_guessing_or_adding_a_day() -> (
    None
):
    params = {"summary": "수업 준비", "start": "2026-09-20", "end": "2026-09-21"}
    google = ScriptedGoogle(
        ("GET", GoogleApiError(404)), ("POST", {}), ("GET", created)
    )
    assert execute(google, parameters=params)["status"] == "COMPLETED"
    assert google.created["start"] == {"date": "2026-09-20"}
    assert google.created["end"] == {"date": "2026-09-21"}


def test_readback_can_normalize_offsets_without_changing_the_approved_instants() -> (
    None
):
    def normalized(google: ScriptedGoogle) -> Mapping[str, Any]:
        value = dict(created(google))
        value["start"] = {"dateTime": "2026-09-20T00:00:00Z", "timeZone": "Asia/Seoul"}
        value["end"] = {
            "dateTime": "2026-09-20T00:30:00+00:00",
            "timeZone": "Asia/Seoul",
        }
        return value

    google = ScriptedGoogle(
        ("GET", GoogleApiError(404)), ("POST", {}), ("GET", normalized)
    )
    assert execute(google)["verified"] is True


@pytest.mark.parametrize(
    "extra",
    [
        "attendees",
        "recurrence",
        "visibility",
        "calendarId",
        "source_ref",
        "sendUpdates",
        "reminders",
        "conferenceData",
    ],
)
def test_unapproved_parameter_fields_are_rejected_before_any_http_request(
    extra: str,
) -> None:
    google = ScriptedGoogle()
    result = execute(google, parameters={**PARAMETERS, extra: []})
    assert result["error_code"] == "INVALID_CALENDAR_PARAMETERS"
    assert not google.calls


@pytest.mark.parametrize(
    "changes",
    [
        {"summary": " "},
        {"summary": "x" * 1001},
        {"summary": "hidden\nlabel"},
        {"description": "x" * 2001},
        {"start": "2026-09-20T09:00:00"},
        {"start": "2026-09-20T09:00:00-00:00"},
        {"start": "2026-09-20T09:00:00+08:60"},
        {"start": "2026-09-20"},
        {"start": "2026-02-30T09:00:00+09:00"},
        {"end": PARAMETERS["start"]},
        {"end": "2026-09-20T08:00:00+09:00"},
        {"timeZone": "../invalid"},
        {"timeZone": "America/New_York"},
        {"summary": None},
        {"start": {"date": "2026-09-20"}},
        {"description": "\ud800"},
    ],
)
def test_invalid_values_fail_before_network_and_without_exposing_input(
    changes: dict[str, Any],
) -> None:
    google = ScriptedGoogle()
    result = execute(google, parameters={**PARAMETERS, **changes})
    assert result["status"] == "FAILED"
    assert result["error_code"] == "INVALID_CALENDAR_PARAMETERS"
    assert not google.calls


def test_timezone_validation_covers_dst_and_normalization_is_idempotent() -> None:
    params = {
        "summary": "DST interval",
        "start": "2026-11-01T01:30:00-04:00",
        "end": "2026-11-01T01:30:00-05:00",
        "timeZone": "America/New_York",
        "description": "",
    }
    normalized = normalize_calendar_parameters(params)
    assert "description" not in normalized
    assert normalize_calendar_parameters(normalized) == normalized
    with pytest.raises(ValueError):
        normalize_calendar_parameters(
            {
                **params,
                "start": "2026-03-08T02:30:00-05:00",
                "end": "2026-03-08T04:00:00-04:00",
            }
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/calendar/event?eid=secret",
        "javascript:alert(1)",
        "https://www.google.com/url?url=https://example.invalid",
        "https://www.google.com:443/calendar/event?eid=x",
        "https://user@calendar.google.com/calendar/event?eid=x",
        "https://calendar.google.com/calendar/event?eid=x&redirect=bad",
    ],
)
def test_returned_links_are_optional_and_restricted_to_calendar_event_views(
    url: str,
) -> None:
    def response(google: ScriptedGoogle) -> Mapping[str, Any]:
        return {**created(google), "htmlLink": url}

    google = ScriptedGoogle(
        ("GET", GoogleApiError(404)), ("POST", {}), ("GET", response)
    )
    result = execute(google)
    assert result["verified"] is True
    assert result["html_url"] is None


@pytest.mark.parametrize(
    "post_response", ["accepted", "conflict", "timeout", "decode_failure"]
)
def test_real_urllib_adapter_recovers_committed_writes_with_scripted_responses(
    monkeypatch: pytest.MonkeyPatch, post_response: str
) -> None:
    calls: list[str] = []
    saved: dict[str, Any] | None = None

    class Response:
        def __init__(self, raw: bytes) -> None:
            self.raw = raw

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def read(self) -> bytes:
            return self.raw

    def urlopen(request: Any, *, timeout: float) -> Response:
        nonlocal saved
        calls.append(request.get_method())
        assert request.headers["Authorization"] == "Bearer synthetic-access-token"
        assert timeout == 10.0
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)
        if request.get_method() == "POST":
            saved = json.loads(request.data)
            assert "attendees" not in saved
            assert saved["reminders"] == {"useDefault": False}
            if post_response == "conflict":
                raise urllib.error.HTTPError(
                    request.full_url, 409, "duplicate", {}, None
                )
            if post_response == "timeout":
                raise urllib.error.URLError(
                    TimeoutError("private transport diagnostic")
                )
            if post_response == "decode_failure":
                return Response(b"not-json")
            return Response(b"{}")
        assert saved is not None
        return Response(json.dumps({**saved, "htmlLink": LINK}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = GoogleCalendarExecutor(UrlLibGoogleHttpClient()).execute(
        access_token="synthetic-access-token",
        operation_id="approved-action",
        parameters=PARAMETERS,
    )
    assert result["status"] == "COMPLETED"
    assert result["html_url"] == LINK
    assert calls == ["GET", "POST", "GET"]
    assert "private transport diagnostic" not in json.dumps(result)
