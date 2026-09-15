import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "contracts" / "events" / "event-envelope.schema.json").read_text()
)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def valid_event() -> dict:
    return {
        "schema_version": 1,
        "event_id": "01EVENT",
        "event_type": "GMAIL_HISTORY_AVAILABLE",
        "user_id": "user-1",
        "connector": "google",
        "occurred_at": "2026-08-23T00:00:00Z",
        "dedupe_key": "google:user-1:42",
        "trace_id": "01TRACE",
        "payload": {"history_id": "42"},
    }


def test_event_envelope_accepts_known_event() -> None:
    VALIDATOR.validate(valid_event())


def test_event_envelope_rejects_unknown_event_type() -> None:
    event = valid_event()
    event["event_type"] = "RUN_ARBITRARY_TOOL"
    with pytest.raises(ValidationError):
        VALIDATOR.validate(event)
