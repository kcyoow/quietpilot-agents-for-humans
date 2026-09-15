"""Bounded, deterministic execution of an already approved private Calendar event.

The caller owns current user/grant/plan authorization and stable operation identity.
This module never obtains tokens, changes an existing event, or retries a write in
one invocation. Only a matching events.get response can certify completion.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from http.client import HTTPException
from typing import Any, Literal, TypedDict
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .google_connector import GoogleApiError, GoogleHttpClient

CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
    r"(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)",
    re.ASCII,
)
_REQUIRED_PARAMETERS = {"summary", "start", "end"}
_OPTIONAL_PARAMETERS = {"description", "timeZone"}
_UNCONFIRMED_RESPONSE_ERRORS = (
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    HTTPException,
)


class CalendarExecutionResult(TypedDict):
    status: Literal["COMPLETED", "VERIFYING", "FAILED"]
    result_ref: str | None
    html_url: str | None
    verified: bool
    error_code: str | None


def normalize_calendar_parameters(parameters: Mapping[str, object]) -> dict[str, str]:
    """Validate the fixed event_create input without guessing dates or time zones.

    All-day end dates are exclusive. Timed values require a known UTC offset;
    an optional IANA zone must agree with that offset at both instants. Empty
    descriptions normalize to absence. Invalid input never appears in errors.
    """
    if (
        not isinstance(parameters, Mapping)
        or not _REQUIRED_PARAMETERS <= parameters.keys()
        or parameters.keys() - _REQUIRED_PARAMETERS - _OPTIONAL_PARAMETERS
    ):
        raise ValueError("Invalid Calendar parameters")
    summary = _text(parameters["summary"], 1000, multiline=False)
    if not summary.strip():
        raise ValueError("Invalid Calendar summary")
    start = _text(parameters["start"], 40, multiline=False)
    end = _text(parameters["end"], 40, multiline=False)
    normalized = {"summary": summary}
    if _DATE.fullmatch(start) and _DATE.fullmatch(end):
        if "timeZone" in parameters or date.fromisoformat(end) <= date.fromisoformat(
            start
        ):
            raise ValueError("Invalid Calendar date range")
        normalized.update(start=start, end=end)
    else:
        start_time, end_time = _datetime(start), _datetime(end)
        if end_time <= start_time:
            raise ValueError("Invalid Calendar time range")
        normalized.update(start=_iso(start_time), end=_iso(end_time))
        if "timeZone" in parameters:
            zone_name = _text(parameters["timeZone"], 128, multiline=False)
            try:
                zone = ZoneInfo(zone_name)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError("Invalid Calendar time zone") from None
            if any(
                value.astimezone(zone).utcoffset() != value.utcoffset()
                for value in (start_time, end_time)
            ):
                raise ValueError("Calendar time zone and offset disagree")
            normalized["timeZone"] = zone_name
    if "description" in parameters:
        description = _text(parameters["description"], 2000, multiline=True)
        if description:
            normalized["description"] = description
    return normalized


@dataclass(frozen=True, slots=True)
class GoogleCalendarExecutor:
    http: GoogleHttpClient

    def execute(
        self,
        *,
        access_token: str,
        operation_id: str,
        parameters: Mapping[str, object],
    ) -> CalendarExecutionResult:
        if not isinstance(access_token, str) or not access_token.strip():
            return _result("FAILED", None, "GOOGLE_AUTH_REQUIRED")
        try:
            operation = _text(operation_id, 256, multiline=False)
            if not operation.strip():
                raise ValueError("Invalid operation identity")
            normalized = normalize_calendar_parameters(parameters)
        except (TypeError, ValueError, OverflowError):
            return _result("FAILED", None, "INVALID_CALENDAR_PARAMETERS")
        operation_hash = _digest(
            {"operation": "calendar.event_create.v1", "id": operation}
        )
        event_id = "qp" + operation_hash  # Hex plus q/p is valid Calendar base32hex.
        result_ref = f"google-calendar:primary:{event_id}"
        body = _event_body(event_id, operation_hash, normalized)
        event_url = f"{CALENDAR_EVENTS_URL}/{event_id}"
        try:
            existing = self.http.request_json(
                "GET", event_url, access_token=access_token
            )
        except GoogleApiError as error:
            if error.status_code != 404:
                return _http_failure(error.status_code, result_ref)
        except _UNCONFIRMED_RESPONSE_ERRORS:
            return _result("VERIFYING", result_ref, "CALENDAR_RESULT_UNCONFIRMED")
        else:
            return _verify(existing, body, result_ref)

        try:
            # No attendees, email reminders, conferences, or caller-supplied options.
            self.http.request_json(
                "POST",
                f"{CALENDAR_EVENTS_URL}?sendUpdates=none",
                access_token=access_token,
                body=body,
            )
        except GoogleApiError as error:
            if error.status_code not in {408, 409, 429} and error.status_code < 500:
                return _http_failure(error.status_code, result_ref)
        except _UNCONFIRMED_RESPONSE_ERRORS:
            # Timeout, transport, and response-decode failures may follow a committed write.
            return self._readback(access_token, event_url, body, result_ref)
        return self._readback(access_token, event_url, body, result_ref)

    def _readback(
        self,
        access_token: str,
        event_url: str,
        expected: Mapping[str, Any],
        result_ref: str,
    ) -> CalendarExecutionResult:
        try:
            actual = self.http.request_json("GET", event_url, access_token=access_token)
        except GoogleApiError as error:
            if error.status_code == 404:
                return _result("VERIFYING", result_ref, "CALENDAR_RESULT_UNCONFIRMED")
            return _http_failure(error.status_code, result_ref)
        except _UNCONFIRMED_RESPONSE_ERRORS:
            return _result("VERIFYING", result_ref, "CALENDAR_RESULT_UNCONFIRMED")
        return _verify(actual, expected, result_ref)


def _text(value: object, limit: int, *, multiline: bool) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(
            (ord(character) < 32 and not (multiline and character in "\n\t"))
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        raise ValueError("Invalid Calendar text")
    return value


def _datetime(value: object) -> datetime:
    if (
        not isinstance(value, str)
        or not _DATETIME.fullmatch(value)
        or value.endswith("-00:00")
    ):
        raise ValueError("Calendar time requires a known UTC offset")
    try:
        return datetime.fromisoformat(value.upper())
    except ValueError:
        raise ValueError("Invalid Calendar time") from None


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _event_body(
    event_id: str, operation_hash: str, parameters: Mapping[str, str]
) -> dict[str, Any]:
    time_field = "date" if _DATE.fullmatch(parameters["start"]) else "dateTime"
    body: dict[str, Any] = {
        "id": event_id,
        "summary": parameters["summary"],
        "start": {time_field: parameters["start"]},
        "end": {time_field: parameters["end"]},
        "eventType": "default",
        "status": "confirmed",
        "visibility": "private",
        "reminders": {"useDefault": False},
        "extendedProperties": {
            "private": {
                "quietpilotOperation": operation_hash,
                "quietpilotContent": _digest(parameters),
            }
        },
    }
    if "description" in parameters:
        body["description"] = parameters["description"]
    if "timeZone" in parameters:
        for boundary in ("start", "end"):
            body[boundary]["timeZone"] = parameters["timeZone"]
    return body


def _verify(
    actual: Mapping[str, Any], expected: Mapping[str, Any], result_ref: str
) -> CalendarExecutionResult:
    if not isinstance(actual, Mapping):
        return _result("FAILED", result_ref, "CALENDAR_READBACK_MISMATCH")
    if actual.get("status") == "cancelled":
        return _result("FAILED", result_ref, "CALENDAR_EVENT_CANCELLED")
    properties = actual.get("extendedProperties")
    reminders = actual.get("reminders")
    matches = (
        actual.get("id") == expected["id"]
        and actual.get("status") == "confirmed"
        and actual.get("eventType", "default") == "default"
        and actual.get("visibility") == "private"
        and actual.get("summary") == expected["summary"]
        and actual.get("description", "") == expected.get("description", "")
        and isinstance(properties, Mapping)
        and isinstance(properties.get("private"), Mapping)
        and all(
            properties["private"].get(key) == value
            for key, value in expected["extendedProperties"]["private"].items()
        )
        and isinstance(reminders, Mapping)
        and reminders.get("useDefault") is False
        and reminders.get("overrides") in (None, [])
        and actual.get("attendees") in (None, [])
        and not any(
            actual.get(key)
            for key in (
                "attendeesOmitted",
                "recurrence",
                "recurringEventId",
                "originalStartTime",
                "conferenceData",
                "hangoutLink",
                "attachments",
                "endTimeUnspecified",
            )
        )
        and all(_same_time(actual.get(key), expected[key]) for key in ("start", "end"))
    )
    if not matches:
        return _result("FAILED", result_ref, "CALENDAR_READBACK_MISMATCH")
    result = _result("COMPLETED", result_ref, None)
    result["html_url"] = _safe_html_url(actual.get("htmlLink"))
    return result


def _same_time(actual: object, expected: Mapping[str, str]) -> bool:
    if not isinstance(actual, Mapping):
        return False
    if "date" in expected:
        return actual.get("date") == expected["date"] and not actual.get("dateTime")
    if actual.get("date") or (
        "timeZone" in expected and actual.get("timeZone") != expected["timeZone"]
    ):
        return False
    try:
        return _datetime(actual.get("dateTime")) == _datetime(expected["dateTime"])
    except (ValueError, OverflowError):
        return False


def _safe_html_url(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or any(ord(c) <= 32 for c in value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        query = parse_qs(parsed.query)
        if (
            parsed.scheme == "https"
            and parsed.netloc in {"www.google.com", "calendar.google.com"}
            and parsed.path == "/calendar/event"
            and not parsed.fragment
            and set(query) <= {"eid", "ctz"}
            and len(query.get("eid", [])) == 1
            and query["eid"][0]
        ):
            return value
    except ValueError:
        pass
    return None


def _http_failure(status: int, result_ref: str) -> CalendarExecutionResult:
    if status == 401:
        return _result("FAILED", result_ref, "GOOGLE_AUTH_REQUIRED")
    if status == 403:
        return _result("FAILED", result_ref, "GOOGLE_CALENDAR_FORBIDDEN")
    if status == 410:
        return _result("FAILED", result_ref, "CALENDAR_EVENT_GONE")
    if status in {408, 429} or status >= 500:
        return _result("VERIFYING", result_ref, "GOOGLE_CALENDAR_UNAVAILABLE")
    return _result("FAILED", result_ref, "GOOGLE_CALENDAR_REQUEST_REJECTED")


def _result(
    status: Literal["COMPLETED", "VERIFYING", "FAILED"],
    result_ref: str | None,
    error_code: str | None,
) -> CalendarExecutionResult:
    return {
        "status": status,
        "result_ref": result_ref,
        "html_url": None,
        "verified": status == "COMPLETED",
        "error_code": error_code,
    }
