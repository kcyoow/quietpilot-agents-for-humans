"""Local SDK + Moto proof of fresh routine Calendar preparation and push acceptance."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from quietpilot_agent.agentcore_runtime import _google_interest_response
from quietpilot_agent.google_connector import GoogleConnector
from quietpilot_agent.local_model import DeterministicModel, ModelPlan, ToolStep
from quietpilot_agent.mail_interests import MailInterestProfile
from quietpilot_control_api.connections import SqsWorkQueue
from quietpilot_control_api.handlers import handle_request
from quietpilot_control_api.mail import DynamoMailStore, MailInterestService
from quietpilot_control_api.notifications import PushTokenService
from quietpilot_control_api.routines import DynamoRoutineStore as ApiRoutineStore
from quietpilot_control_api.routines import RoutineService
from quietpilot_worker import consumer
from quietpilot_worker.google_connection_store import DynamoConnectionWriter
from quietpilot_worker.google_jobs import _scan_page_details
from quietpilot_worker.mail_jobs import DynamoMailJobStore
from quietpilot_worker.notifications import ExpoPushTransport, NotificationDispatcher
from quietpilot_worker.routines import DynamoRoutineStore as WorkerRoutineStore
from quietpilot_worker.routines import RoutineProcessor, SqsRoutineCaseQueue

from tests.test_calendar_flow import (
    BODY_MARKER,
    OAUTH_TOKEN,
    OWNER,
    RAW_ID,
    REF,
    TABLE,
    FlowModels,
    _event,
)
from tests.test_calendar_flow import flow as _calendar_flow

flow = _calendar_flow

NEXT_RAW_ID = "2b3c4d5e6f7890a1"
NEXT_REF = "gmail:" + hashlib.sha256(NEXT_RAW_ID.encode()).hexdigest()
NEXT_START, NEXT_END = "2030-06-12T14:30:00+09:00", "2030-06-12T15:45:00+09:00"
NEXT_TITLE = "다음 상담 예약 확정"
NEXT_BODY = (
    f"신청하신 다음 상담 예약이 확정되었습니다. 시작은 {NEXT_START}, "
    f"종료는 {NEXT_END}입니다. {BODY_MARKER}"
)
PUSH_TOKEN = "ExpoPushToken[synthetic_routine_flow_token]"


class NextSourceHTTP:
    def __init__(self, previous, received):
        self.previous = previous
        self.calls, self.events = previous.calls, previous.events
        self.title, self.body = NEXT_TITLE, NEXT_BODY
        self.received = str(int(received.timestamp() * 1000))

    def request_json(self, method, url, *, access_token, body=None):
        parsed, query = urlsplit(url), parse_qs(urlsplit(url).query)
        if parsed.path == "/gmail/v1/users/me/messages":
            assert method == "GET" and query["maxResults"] == ["250"]
            assert "after:" in query["q"][0] and "before:" in query["q"][0]
            assert access_token == OAUTH_TOKEN
            self.calls.append((method, parsed.path))
            return {"messages": [{"id": NEXT_RAW_ID}]}
        if parsed.path != f"/gmail/v1/users/me/messages/{NEXT_RAW_ID}":
            return self.previous.request_json(
                method, url, access_token=access_token, body=body
            )
        assert method == "GET" and query["format"] == ["full"]
        assert access_token == OAUTH_TOKEN
        self.calls.append((method, parsed.path))
        return {
            "id": NEXT_RAW_ID,
            "internalDate": self.received,
            "snippet": "",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Subject", "value": NEXT_TITLE},
                    {"name": "From", "value": "School <notify@school.example>"},
                ],
                "body": {
                    "data": base64.urlsafe_b64encode(NEXT_BODY.encode())
                    .decode()
                    .rstrip("=")
                },
            },
        }


class NextSourceModels(FlowModels):
    def create(self, role, plan):
        if role not in {"calendar_draft_planner", "calendar_draft_verifier"}:
            return super().create(role, plan)
        payload = {
            "decision": "PROPOSE",
            "event_kind": "TIMED_EVENT",
            "source_ref": "m1",
            "summary": "Next consultation appointment",
            "start": NEXT_START,
            "end": NEXT_END,
            "time_zone": "",
            "description": "",
            "start_quote_ref": "m1p1",
            "end_quote_ref": "m1p1",
            "purpose_quote_ref": "m1p1",
            "reason": "The new email confirms the appointment’s start and end.",
        }
        model = DeterministicModel(
            role,
            ModelPlan(
                steps=(ToolStep("CalendarDraftAssessment", payload),),
                output=plan.output,
            ),
        )
        self.models.append((role, model))
        return model


class ScriptedExpo:
    def __init__(self):
        self.sent = []

    def open(self, request, *, timeout):
        assert request.full_url == "https://exp.host/--/api/v2/push/send"
        assert request.get_method() == "POST" and timeout <= 5
        self.sent.append(json.loads(request.data))
        response = io.BytesIO(b'{"data":{"status":"ok","id":"synthetic-ticket"}}')
        response.status = 200
        return response


def _get(flow, pk, sk):
    return flow.db.get_item(
        TableName=TABLE,
        Key={"PK": {"S": pk}, "SK": {"S": sk}},
        ConsistentRead=True,
    ).get("Item", {})


def _case_rows(flow, case_id):
    return flow.db.query(
        TableName=TABLE,
        KeyConditionExpression="PK=:pk",
        ExpressionAttributeValues={":pk": {"S": f"CASE#{case_id}"}},
        ConsistentRead=True,
    )["Items"]


def _routine_api(flow, service, path, body):
    response = handle_request(
        _event("POST", path, body),
        workspace_service=flow.service,
        routine_service=service,
    )
    assert response["statusCode"] == 200, response
    return json.loads(response["body"])["routine"]


def _complete_first_calendar_case(flow):
    flow.seed()
    flow.discover_and_persist()
    prepared = flow.prepare()
    assert prepared["status"] == "DECISION_REQUIRED"
    flow.api(
        "POST",
        f"/v1/cases/{prepared['case_id']}/decision",
        {
            "decision": "APPROVE",
            "expected_version": prepared["version"],
            "plan_version": prepared["plan"]["version"],
            "plan_hash": prepared["plan"]["hash"],
            "grant_mode": "ONCE",
        },
        template="/v1/cases/{case_id}/decision",
    )
    flow.execution_worker.process(flow.pop("ACTION_EXECUTE_REQUESTED"))
    completed = flow.api(
        "GET", f"/v1/cases/{prepared['case_id']}", template="/v1/cases/{case_id}"
    )
    assert completed["status"] == "COMPLETED"
    assert completed["actions"][0]["verified"] is True
    assert completed["actions"][0]["status"] == "SUCCEEDED"
    assert len(flow.google.events) == 1
    return completed


def _discover_next_mail(flow):
    mail = MailInterestService(
        DynamoMailStore(TABLE, flow.db), SqsWorkQueue(flow.queue_url, flow.sqs)
    )
    requested = mail.scan(OWNER)
    assert requested["scan"]["status"] == "PENDING"
    flow.pop("MAIL_SCAN_REQUESTED")
    source = {
        "status": "SCAN_PAGE",
        "processed_message_count": 1,
        "recent_message_estimate": 1,
        "next_page_token": None,
        "completion_history_id": "101",
        "evidence": [
            flow.runtime.connector._message_evidence(
                access_token=OAUTH_TOKEN, message_id=NEXT_RAW_ID
            )
        ],
    }
    result = _google_interest_response(
        user_id=OWNER,
        profile=MailInterestProfile(revision=1, tags=["일정"]),
        source=source,
        model_factory=flow.models,
    )
    assert result["discovery_validated"] and result["unresolved_evidence_count"] == 0
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["evidence_refs"] == [NEXT_REF]
    assert result["candidates"][0]["opportunity_type"] == "APPOINTMENT"
    store = DynamoMailJobStore(TABLE, flow.db)
    state, item, connection = store.load(OWNER)
    store.persist(
        OWNER,
        state,
        item,
        connection["mail_connection_id"]["S"],
        result,
        _scan_page_details(result, interest_results=True),
        DynamoConnectionWriter(TABLE, flow.db),
        step=0,
        next_work=None,
    )
    return result


def test_verified_calendar_routine_prepares_fresh_mail_and_dispatches_exact_attention(
    flow, monkeypatch
):
    completed = _complete_first_calendar_case(flow)
    original_events = copy.deepcopy(flow.google.events)
    original_parameters = completed["plan"]["actions"][0]["parameters"]
    activated_at = datetime.now(UTC)
    routines = RoutineService(
        ApiRoutineStore(TABLE, flow.db), clock=lambda: activated_at
    )
    proposed = _routine_api(
        flow,
        routines,
        "/v1/routines",
        {"case_id": completed["case_id"], "expected_version": completed["version"]},
    )
    assert proposed["status"] == "PROPOSED" and proposed["mode"] == "PREPARE_ONLY"
    active = _routine_api(
        flow,
        routines,
        f"/v1/routines/{proposed['routine_id']}/activate",
        {"expected_version": proposed["version"]},
    )
    assert active["effective_status"] == "ACTIVE"
    assert active["sender_domain"] == "school.example"
    assert active["opportunity_type"] == "APPOINTMENT"

    flow.google = NextSourceHTTP(flow.google, activated_at + timedelta(seconds=1))
    flow.models = NextSourceModels()
    flow.models.forbid_duplicate_calendar_graph = True
    flow.runtime.models = flow.models
    flow.runtime.connector = GoogleConnector(
        flow.identity, flow.google, oauth_return_url="https://example.test/return"
    )
    new_mail = _discover_next_mail(flow)
    processor = RoutineProcessor(
        WorkerRoutineStore(TABLE, flow.db),
        SqsRoutineCaseQueue(flow.queue_url, flow.sqs),
        clock=lambda: activated_at + timedelta(seconds=2),
    )
    stats = processor.process(OWNER)
    assert stats["created"] == stats["dispatched"] == 1, stats
    assert not stats["limit_reached"]
    job = flow.pop("DIRECT_REQUEST_RECEIVED")
    case_id = job["payload"]["case_id"]
    before = _get(flow, f"CASE#{case_id}", "META")
    assert before["case_type"] == {"S": "ROUTINE_DISCOVERY"}
    assert before["routine_mode"] == {"S": "PREPARE_ONLY"}
    assert before["origin_routine_id"] == {"S": active["routine_id"]}
    assert before["evidence_refs"] == {"L": [{"S": NEXT_REF}]}
    assert NEXT_REF != REF
    assert original_parameters["start"] not in json.dumps(_case_rows(flow, case_id))
    duplicate = processor.process(OWNER)
    assert duplicate["created"] == duplicate["dispatched"] == 0
    assert not flow.sqs.receive_message(QueueUrl=flow.queue_url).get("Messages")

    monkeypatch.setenv("MAIN_TABLE_NAME", TABLE)
    monkeypatch.setenv("WORK_QUEUE_URL", flow.queue_url)
    monkeypatch.setattr(
        consumer, "default_case_job_processor", lambda: flow.case_worker
    )
    consumer._foundation_processor(job)
    prepared = flow.api("GET", f"/v1/cases/{case_id}", template="/v1/cases/{case_id}")
    assert prepared["status"] == "DECISION_REQUIRED"
    assert prepared["plan"]["available_grant_modes"] == ["ONCE"], {
        "plan": prepared["plan"],
        "roles": [role for role, _ in flow.models.models],
        "runtime_error": flow.runtime.results[-1].get("error_code"),
    }
    assert prepared["plan"]["evidence_revisions"] == {NEXT_REF: 1}
    action = prepared["plan"]["actions"][0]
    assert action["verb"] == "calendar_event_create" and action["risk"] == "MEDIUM"
    assert action["parameters"] == {
        "summary": "Next consultation appointment",
        "start": NEXT_START,
        "end": NEXT_END,
        "source_ref": NEXT_REF,
    }
    assert flow.google.events == original_events
    assert len([method for method, _ in flow.google.calls if method == "POST"]) == 1
    assert [
        role for role, _ in flow.models.models if role.startswith("calendar_draft")
    ] == ["calendar_draft_planner", "calendar_draft_verifier"]
    assert sum(path.endswith("/" + NEXT_RAW_ID) for _, path in flow.google.calls) == 2
    assert sum(path.endswith("/" + RAW_ID) for _, path in flow.google.calls) == 2
    case_rows = _case_rows(flow, case_id)
    assert not any(
        row["SK"]["S"].startswith(("APPROVAL#", "ACTION#")) for row in case_rows
    )
    meta = _get(flow, f"CASE#{case_id}", "META")
    assert meta["version"] == {"N": str(prepared["version"])}
    assert int(meta["version"]["N"]) == int(before["version"]["N"]) + 1

    dispatch = flow.pop("NOTIFICATION_DISPATCH_REQUESTED")
    notification_id = dispatch["payload"]["notification_id"]
    event = _get(flow, f"USER#{OWNER}", f"NOTIFICATION#{notification_id}")
    assert event["case_id"] == {"S": case_id}
    assert event["version"] == meta["version"]
    assert event["kind"] == {"S": "DECISION_REQUIRED"}
    assert event["status"] == {"S": "PENDING"}
    registration = handle_request(
        _event(
            "PUT",
            "/v1/mobile/push-token",
            {
                "expo_push_token": PUSH_TOKEN,
                "device_id": "routine-device-one",
                "app_version": "1.0.0",
                "platform": "android",
            },
        ),
        push_service=PushTokenService(TABLE, flow.db),
    )
    assert registration["statusCode"] == 204 and registration["body"] == ""
    expo = ScriptedExpo()
    dispatcher = NotificationDispatcher(
        TABLE, flow.db, transport=ExpoPushTransport(opener=expo)
    )
    counts = dispatcher.flush(OWNER, event_id=notification_id)
    assert counts == {
        "accepted": 1,
        "rejected": 0,
        "unknown": 0,
        "suppressed": 0,
        "busy": 0,
    }
    assert expo.sent == [
        {
            "to": PUSH_TOKEN,
            "title": "Review needed",
            "body": "A task needs your review. Open the app to continue.",
            "data": {
                "case_id": case_id,
                "event_id": notification_id,
                "kind": "DECISION_REQUIRED",
            },
            "channelId": "default",
            "ttl": 300,
        }
    ]
    accepted = _get(flow, f"USER#{OWNER}", f"NOTIFICATION#{notification_id}")
    assert accepted["status"] == {"S": "ACCEPTED"}
    assert "delivered" not in accepted and "verified" not in accepted
    assert dispatcher.flush(OWNER, event_id=notification_id)["accepted"] == 0
    assert len(expo.sent) == 1
    consumer._foundation_processor(job)
    assert not flow.sqs.receive_message(QueueUrl=flow.queue_url).get("Messages")
    assert (
        len(
            [
                row
                for row in _case_rows(flow, case_id)
                if row["SK"]["S"].startswith("PLAN#")
            ]
        )
        == 1
    )
    repeat = processor.process(OWNER)
    assert repeat["created"] == repeat["dispatched"] == 0
    assert not flow.sqs.receive_message(QueueUrl=flow.queue_url).get("Messages")
    owned_cases = [
        row
        for row in flow.db.scan(TableName=TABLE)["Items"]
        if row.get("entity_type") == {"S": "case"}
        and row.get("user_id") == {"S": OWNER}
    ]
    assert {row["case_id"]["S"] for row in owned_cases} == {
        completed["case_id"],
        case_id,
    }
    assert flow.google.events == original_events
    assert new_mail["candidates"][0]["outcome"] not in json.dumps(
        expo.sent, ensure_ascii=False
    )
    for sensitive in (NEXT_BODY, NEXT_RAW_ID, NEXT_START, NEXT_TITLE, OAUTH_TOKEN):
        assert sensitive not in json.dumps(expo.sent, ensure_ascii=False)
    flow.assert_no_source_or_credentials_persisted()
    assert not flow.contract_errors, flow.contract_errors
