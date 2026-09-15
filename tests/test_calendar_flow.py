"""Offline cross-service Calendar flow with real SDK, Moto and scripted Google.

Run: uv run --offline --all-packages --with 'moto[dynamodb,sqs]' pytest -q tests/test_calendar_flow.py
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from quietpilot_agent.agentcore_runtime import (
    _google_interest_response,
    run_agentcore_invocation,
)
from quietpilot_agent.google_connector import (
    CALENDAR_EVENTS_SCOPE,
    GMAIL_READONLY_SCOPE,
    GoogleApiError,
    GoogleConnector,
)
from quietpilot_agent.local_model import DeterministicModel, ModelPlan, ToolStep
from quietpilot_agent.mail_interests import MailInterestProfile
from quietpilot_control_api.calendar_approval import CalendarApprovalService
from quietpilot_control_api.connections import SqsWorkQueue
from quietpilot_control_api.handlers import handle_request
from quietpilot_control_api.mail import empty_state
from quietpilot_control_api.suggestions import DynamoSuggestionStore, SuggestionService
from quietpilot_control_api.workspace import (
    DynamoWorkspaceStore,
    SqsCasePreparationQueue,
    WorkspaceService,
)
from quietpilot_worker.calendar_execution import (
    CalendarExecutionProcessor,
    DynamoCalendarExecutionStore,
)
from quietpilot_worker.case_jobs import CaseJobProcessor, DynamoCasePreparationStore
from quietpilot_worker.google_connection_store import DynamoConnectionWriter
from quietpilot_worker.google_jobs import _scan_page_details
from quietpilot_worker.mail_jobs import DynamoMailJobStore

moto = pytest.importorskip(
    "moto", reason="Use the documented ephemeral Moto dependency"
)
try:
    import yaml
except ImportError:
    yaml = None

TABLE = "calendar-full-flow"
OWNER = "synthetic-owner"
EPOCH = "synthetic-connection-epoch"
SCAN = "a" * 32
EMAIL = "calendar-owner@example.test"
ACCOUNT = hashlib.sha256(EMAIL.encode()).hexdigest()
RAW_ID = "1a2b3c4d5e6f7890"
REF = "gmail:" + hashlib.sha256(RAW_ID.encode()).hexdigest()
RECEIVED_MS = "1789344000000"
TITLE = "상담 예약 확정"
OAUTH_TOKEN = "SYNTHETIC-OAUTH-TOKEN-NOT-PERSISTED"
BODY_MARKER = "SYNTHETIC-TRANSIENT-BODY-NOT-PERSISTED"
SOURCE_BODY = (
    "신청하신 상담 예약이 확정되었습니다. "
    "시작은 2030-05-01T10:00:00+09:00, 종료는 2030-05-01T11:00:00+09:00입니다. "
    + BODY_MARKER
)


class FlowModels:
    def __init__(self):
        self.models = []
        self.calendar_abstain = False
        self.calendar_invalid_role = None
        self.forbid_duplicate_local_graph = False
        self.forbid_duplicate_calendar_graph = False
        self.local_status = "READY"
        self.local_artifact_type = "REMINDER"
        self.local_content = None

    def create(self, role, plan):
        if (
            self.forbid_duplicate_local_graph or self.forbid_duplicate_calendar_graph
        ) and role in {
            "orchestrator",
            "signal_analyst",
            "capability_analyst",
            "case_planner",
        }:
            raise AssertionError("A verified preparation must not be generated again")
        if role in {"mail_interest_matcher", "mail_interest_verifier"}:
            payload = {
                "m1_tag_refs": "t1",
                "m1_description_match": "NO",
                "m1_excluded": "NO",
                "m1_importance": "NORMAL",
                "m1_summary": "Lists the required documents and submission deadline."
                if self.local_artifact_type == "CHECKLIST"
                else "Confirms the requested appointment’s start and end times.",
                "m1_reason": "Matches the saved appointment topic.",
                "m1_source_ref": "m1p1",
            }
            plan = ModelPlan(
                steps=(ToolStep("MailFieldAssessment", payload),), output=plan.output
            )
        elif role in {"calendar_draft_planner", "calendar_draft_verifier"}:
            payload = {
                "decision": "PROPOSE",
                "event_kind": "TIMED_EVENT",
                "source_ref": "m1",
                "summary": "Consultation appointment",
                "start": "2030-05-01T10:00:00+09:00",
                "end": "2030-05-01T11:00:00+09:00",
                "time_zone": "",
                "description": "",
                "start_quote_ref": "m1p1",
                "end_quote_ref": "m1p1",
                "purpose_quote_ref": "m1p1",
                "reason": "The source confirms the appointment’s start and end.",
            }
            if self.calendar_abstain:
                payload = {
                    **{key: "" for key in payload},
                    "decision": "ABSTAIN",
                    "event_kind": "NONE",
                    "reason": "The replacement date is not confirmed.",
                }
            if self.calendar_invalid_role == role:
                payload["start"] = "unconfirmed-date"
            plan = ModelPlan(
                steps=(ToolStep("CalendarDraftAssessment", payload),)
                * (2 if self.calendar_invalid_role == role else 1),
                output=plan.output,
            )
        elif role in {"local_artifact_writer", "local_artifact_verifier"}:
            checklist = self.local_artifact_type == "CHECKLIST"
            payload = {
                "status": "READY",
                "artifact_type": self.local_artifact_type,
                "title": "Application checklist"
                if checklist
                else "Appointment reminder text",
                "content": (
                    "- Complete the application using the required form\n- Prepare proof of enrollment\n- Submit the documents by 2030-05-01"
                    if checklist
                    else "The appointment starts at 2030-05-01T10:00:00+09:00 and ends at 2030-05-01T11:00:00+09:00."
                ),
                "question": "",
                "explanation": "The checklist preserves the requested documents and deadline."
                if checklist
                else "The reminder preserves the confirmed appointment’s start and end.",
                "source_ref": "m1",
                "support_quote_ref": "m1p1",
                "user_fact_ref": "",
                "validation_reason": "Only source-supported content was prepared; no external changes were made.",
            }
            if self.local_status == "NEEDS_INPUT":
                payload.update(
                    status="NEEDS_INPUT",
                    artifact_type="NONE",
                    title="Confirm replacement date",
                    content="",
                    question="What date and time should replace this appointment?",
                    explanation="A replacement date and time are needed before preparing a new event.",
                    user_fact_ref="m2p1",
                    validation_reason="The latest user correction does not provide a replacement date.",
                )
            elif self.local_status == "NO_ACTION":
                payload.update(
                    status="NO_ACTION",
                    artifact_type="NONE",
                    title="",
                    content="",
                    question="",
                    explanation="The source says no further action is required.",
                    validation_reason="The complete source confirms that no further action is required.",
                )
            elif self.local_content is not None:
                payload["content"] = self.local_content
            plan = ModelPlan(
                steps=(ToolStep("LocalPreparationAssessment", payload),),
                output=plan.output,
            )
        model = DeterministicModel(role, plan)
        self.models.append((role, model))
        return model


class ScriptedIdentity:
    def __init__(self):
        self.calls = []

    def get_resource_oauth2_token(self, **payload):
        assert payload["scopes"] in (
            [GMAIL_READONLY_SCOPE],
            [GMAIL_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE],
        )
        self.calls.append(payload)
        return {"accessToken": OAUTH_TOKEN}


class ScriptedGoogleHTTP:
    def __init__(self):
        self.events = {}
        self.calls = []
        self.title = TITLE
        self.body = SOURCE_BODY

    def request_json(self, method, url, *, access_token, body=None):
        assert access_token == OAUTH_TOKEN
        parsed, query = urlsplit(url), parse_qs(urlsplit(url).query)
        self.calls.append((method, parsed.path))
        if parsed.path == "/gmail/v1/users/me/profile":
            assert method == "GET"
            return {"emailAddress": EMAIL}
        if parsed.path == "/gmail/v1/users/me/messages":
            assert method == "GET" and query["maxResults"] == ["250"]
            assert "after:" in query["q"][0] and "before:" in query["q"][0]
            assert "labelIds" not in query
            return {"messages": [{"id": RAW_ID}]}
        if parsed.path == f"/gmail/v1/users/me/messages/{RAW_ID}":
            assert method == "GET" and query["format"] == ["full"]
            return {
                "id": RAW_ID,
                "internalDate": RECEIVED_MS,
                "snippet": "",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject", "value": self.title},
                        {"name": "From", "value": "School <notify@school.example>"},
                    ],
                    "body": {
                        "data": base64.urlsafe_b64encode(self.body.encode())
                        .decode()
                        .rstrip("=")
                    },
                },
            }
        base = "/calendar/v3/calendars/primary/events"
        if method == "POST":
            assert parsed.path == base and query == {"sendUpdates": ["none"]}
            assert body["id"] not in self.events
            self.events[body["id"]] = {
                **copy.deepcopy(body),
                "htmlLink": "https://calendar.google.com/calendar/event?eid=synthetic",
            }
            return copy.deepcopy(self.events[body["id"]])
        assert method == "GET" and parsed.path.startswith(base + "/")
        event = self.events.get(parsed.path.rsplit("/", 1)[1])
        if event is None:
            raise GoogleApiError(404)
        return copy.deepcopy(event)


class LocalRuntime:
    """Use the actual Runtime dispatcher in place of its network transport."""

    def __init__(self, models, google, identity):
        self.models = models
        self.connector = GoogleConnector(
            identity, google, oauth_return_url="https://example.test/return"
        )
        self.calls = []
        self.results = []

    def invoke_payload(self, user_id, payload):
        assert (
            payload.get("user_id", payload.get("request", {}).get("user_id"))
            == user_id
            == OWNER
        )
        self.calls.append(copy.deepcopy(payload))
        result = run_agentcore_invocation(
            payload,
            SimpleNamespace(),
            model_factory=self.models,
            google_connector=self.connector,
            workload_access_token="synthetic-workload-token",
        )
        self.results.append(result)
        return result


def _event(method, path, body=None):
    return {
        "version": "2.0",
        "rawPath": path,
        "headers": {"idempotency-key": "calendar-flow-idempotency"},
        "requestContext": {
            "requestId": "offline-request",
            "routeKey": f"{method} {path}",
            "http": {"method": method, "path": path},
            "authorizer": {"jwt": {"claims": {"sub": OWNER}}},
        },
        **({"body": json.dumps(body, ensure_ascii=False)} if body is not None else {}),
    }


class Flow:
    def __init__(self, db, sqs, queue_url):
        self.db, self.sqs, self.queue_url = db, sqs, queue_url
        self.models, self.google, self.identity = (
            FlowModels(),
            ScriptedGoogleHTTP(),
            ScriptedIdentity(),
        )
        self.runtime = LocalRuntime(self.models, self.google, self.identity)
        self.workspace = DynamoWorkspaceStore(TABLE, db)
        self.service = WorkspaceService(
            self.workspace,
            SqsCasePreparationQueue(queue_url, sqs),
            CalendarApprovalService(self.workspace, SqsWorkQueue(queue_url, sqs)),
        )
        self.suggestions = SuggestionService(DynamoSuggestionStore(TABLE, db))
        self.case_worker = CaseJobProcessor(
            self.runtime, DynamoCasePreparationStore(TABLE, db)
        )
        self.execution_worker = CalendarExecutionProcessor(
            self.runtime, DynamoCalendarExecutionStore(TABLE, db)
        )
        self.responses = []
        self.contract_errors = []
        self.mail_result = None

    def seed(self, *, calendar=True):
        state = empty_state()
        state["profile"].update(tags=["일정"], version=1)
        state["scan"].update(status="PENDING", scan_id=SCAN, profile_version=1)
        for suffix in ["google", "google-calendar"] if calendar else ["google"]:
            self.db.put_item(
                TableName=TABLE,
                Item={
                    "PK": {"S": f"USER#{OWNER}"},
                    "SK": {"S": f"CONNECTION#{suffix}"},
                    "status": {"S": "CONNECTED"},
                    "mail_connection_id": {"S": EPOCH},
                    "account_hash": {"S": ACCOUNT},
                    "gmail_history_id": {"S": "100"},
                    "granted_scopes": {
                        "L": [
                            {"S": scope}
                            for scope in (
                                [GMAIL_READONLY_SCOPE]
                                if suffix == "google"
                                else [CALENDAR_EVENTS_SCOPE]
                            )
                        ]
                    },
                },
            )
        self.db.put_item(
            TableName=TABLE,
            Item={
                "PK": {"S": f"USER#{OWNER}"},
                "SK": {"S": "MAIL_INTERESTS#google"},
                "profile_version": {"N": "1"},
                "scan_id": {"S": SCAN},
                "scan_connection_id": {"S": EPOCH},
                "scan_step": {"N": "0"},
                **{
                    f"{name}_json": {"S": json.dumps(value, ensure_ascii=False)}
                    for name, value in state.items()
                },
            },
        )

    def discover_and_persist(self):
        connector = GoogleConnector(
            self.identity, self.google, oauth_return_url="https://example.test/return"
        )
        source = {
            "status": "SCAN_PAGE",
            "processed_message_count": 1,
            "recent_message_estimate": 1,
            "next_page_token": None,
            "completion_history_id": "100",
            "evidence": [
                connector._message_evidence(access_token=OAUTH_TOKEN, message_id=RAW_ID)
            ],
        }
        self.mail_result = _google_interest_response(
            user_id=OWNER,
            profile=MailInterestProfile(revision=1, tags=["일정"]),
            source=source,
            model_factory=self.models,
        )
        assert (
            self.mail_result["discovery_validated"]
            and self.mail_result["unresolved_evidence_count"] == 0
        )
        assert len(self.mail_result["candidates"]) == 1
        assert not any(
            role.startswith("calendar_draft") for role, _ in self.models.models
        )
        assert all(
            "calendar_event" not in action["parameters"]
            for candidate in self.mail_result["candidates"]
            for action in candidate["proposed_actions"]
        )
        store = DynamoMailJobStore(TABLE, self.db)
        state, item, connection = store.load(OWNER)
        store.persist(
            OWNER,
            state,
            item,
            connection["mail_connection_id"]["S"],
            self.mail_result,
            _scan_page_details(self.mail_result, interest_results=True),
            DynamoConnectionWriter(TABLE, self.db),
            step=0,
            next_work=None,
        )

    def api(self, method, path, body=None, *, template=None, expected=200):
        response = handle_request(
            _event(method, path, body),
            workspace_service=self.service,
            suggestion_service=self.suggestions,
        )
        assert response["statusCode"] == expected, response
        payload = json.loads(response["body"])
        self.responses.append(payload)
        if yaml is not None:
            spec = yaml.safe_load(
                (Path(__file__).parents[1] / "contracts/openapi.yaml").read_text()
            )
            contract = spec["paths"][template or path][method.lower()]["responses"][
                str(expected)
            ]
            while "$ref" in contract:
                assert contract["$ref"].startswith("#/"), (
                    "Offline contract checks resolve local references only"
                )
                target = spec
                for part in contract["$ref"][2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
                contract = target
            schema = (
                contract.get("content", {}).get("application/json", {}).get("schema")
            )
            if schema is None:
                self.contract_errors.append(
                    f"{method} {template or path}: response JSON schema is missing"
                )
            else:
                errors = Draft202012Validator(
                    {**schema, "components": spec["components"]},
                    format_checker=FormatChecker(),
                ).iter_errors(payload)
                self.contract_errors.extend(
                    f"{method} {template or path}: {list(error.path)}: {error.message}"
                    for error in errors
                )
        return payload

    def pop(self, expected_type):
        messages = self.sqs.receive_message(
            QueueUrl=self.queue_url, MaxNumberOfMessages=1
        ).get("Messages", [])
        assert len(messages) == 1
        message = messages[0]
        envelope = json.loads(message["Body"])
        assert envelope["event_type"] == expected_type
        self.sqs.delete_message(
            QueueUrl=self.queue_url, ReceiptHandle=message["ReceiptHandle"]
        )
        return envelope

    def prepare(self):
        suggestions = self.api("GET", "/v1/suggestions")["suggestions"]
        assert len(suggestions) == 1
        candidate = suggestions[0]
        reference = self.api(
            "POST",
            "/v1/suggestions/convert",
            {
                "candidate_ids": [candidate["candidate_id"]],
                "expected_versions": [candidate["version"]],
            },
            expected=201,
        )
        self.case_worker.process(self.pop("DIRECT_REQUEST_RECEIVED"))
        return self.api(
            "GET", f"/v1/cases/{reference['case_id']}", template="/v1/cases/{case_id}"
        )

    def assert_no_source_or_credentials_persisted(self):
        stored = json.dumps(self.db.scan(TableName=TABLE)["Items"], ensure_ascii=False)
        wire = json.dumps(
            [self.mail_result, self.responses, self.runtime.results], ensure_ascii=False
        )
        for marker in (
            OAUTH_TOKEN,
            BODY_MARKER,
            SOURCE_BODY,
            self.google.body,
            EMAIL,
            RAW_ID,
            "synthetic-workload-token",
        ):
            assert marker not in stored and marker not in wire


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    def deny_network(*args, **kwargs):
        raise AssertionError("This integration test must not open a network socket")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
        db.create_table(
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
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_url = sqs.create_queue(QueueName="calendar-full-flow")["QueueUrl"]
        yield Flow(db, sqs, queue_url)


def test_mail_source_to_exact_approval_one_calendar_resource_and_completed_projection(
    flow,
):
    flow.seed()
    flow.discover_and_persist()
    flow.models.forbid_duplicate_calendar_graph = True
    prepared = flow.prepare()
    assert prepared["status"] == "DECISION_REQUIRED" and prepared["risk"] == "MEDIUM"
    plan = copy.deepcopy(prepared["plan"])
    assert plan["risk"] == "MEDIUM" and plan["required_scopes"] == [
        CALENDAR_EVENTS_SCOPE
    ]
    assert plan["available_grant_modes"] == ["ONCE"]
    assert plan["evidence_revisions"] == {REF: 1}
    action = plan["actions"][0]
    assert action["verb"] == "calendar_event_create" and action["risk"] == "MEDIUM"
    assert (
        action["parameters"]["source_ref"] == REF
        and "calendar_event" not in action["parameters"]
    )
    assert not flow.google.events
    assert (
        len(
            [
                path
                for method, path in flow.google.calls
                if path == f"/gmail/v1/users/me/messages/{RAW_ID}"
            ]
        )
        == 2  # One bounded scan read, then one fresh read for the selected Case.
    )
    assert (
        len(
            [
                role
                for role, _ in flow.models.models
                if role.startswith("calendar_draft")
            ]
        )
        == 2
    )
    assert not any(role.startswith("local_artifact") for role, _ in flow.models.models)
    assert flow.runtime.calls[0]["google_account_hash"] == ACCOUNT
    assert flow.runtime.calls[0]["evidence"][0]["untrusted_text"] is None
    frozen_plan = copy.deepcopy(
        flow.db.get_item(
            TableName=TABLE,
            Key={
                "PK": {"S": f"CASE#{prepared['case_id']}"},
                "SK": {"S": f"PLAN#{plan['version']:06d}"},
            },
            ConsistentRead=True,
        )["Item"]
    )
    before_execution_calls = len(flow.google.calls)
    before_execution_tokens = len(flow.identity.calls)
    body = {
        "decision": "APPROVE",
        "expected_version": prepared["version"],
        "plan_version": plan["version"],
        "plan_hash": plan["hash"],
        "grant_mode": "ONCE",
    }
    path = f"/v1/cases/{prepared['case_id']}/decision"
    flow.api(
        "POST",
        path,
        {**body, "plan_hash": "0" * 64},
        template="/v1/cases/{case_id}/decision",
        expected=409,
    )
    assert len(flow.google.calls) == before_execution_calls and not flow.google.events
    assert not flow.sqs.receive_message(QueueUrl=flow.queue_url).get("Messages")
    queued = flow.api("POST", path, body, template="/v1/cases/{case_id}/decision")
    assert queued["status"] == "QUEUED" and queued["plan"] == plan
    # Exact replay creates a second real SQS delivery, never a second resource.
    flow.api("POST", path, body, template="/v1/cases/{case_id}/decision")
    first = flow.pop("ACTION_EXECUTE_REQUESTED")
    duplicate = flow.pop("ACTION_EXECUTE_REQUESTED")
    assert first["payload"] == duplicate["payload"]
    flow.execution_worker.process(first)
    flow.execution_worker.process(duplicate)
    completed = flow.api(
        "GET", f"/v1/cases/{prepared['case_id']}", template="/v1/cases/{case_id}"
    )
    assert (
        completed["status"] == "COMPLETED" and completed["plan"]["hash"] == plan["hash"]
    )
    assert (
        flow.db.get_item(
            TableName=TABLE,
            Key={"PK": frozen_plan["PK"], "SK": frozen_plan["SK"]},
            ConsistentRead=True,
        )["Item"]
        == frozen_plan
    )
    assert completed["plan"]["actions"][0]["parameters"] == action["parameters"]
    assert len(flow.google.events) == 1
    assert len([method for method, _ in flow.google.calls if method == "POST"]) == 1
    assert [method for method, _ in flow.google.calls[before_execution_calls:]] == [
        "GET",
        "GET",
        "POST",
        "GET",
    ]
    assert len(flow.identity.calls) == before_execution_tokens + 1
    assert completed["actions"][0]["status"] == "SUCCEEDED"
    assert completed["actions"][0]["verified"] is True
    assert completed["actions"][0]["result_ref"].startswith(
        "google-calendar:primary:qp"
    )
    assert any(row["state"] == "DONE" for row in completed["timeline"])
    flow.assert_no_source_or_credentials_persisted()
    assert not flow.contract_errors, flow.contract_errors


def test_mail_preparation_does_not_promote_without_current_calendar_capability(flow):
    flow.seed(calendar=False)
    flow.discover_and_persist()
    calendar_models = len(
        [role for role, _ in flow.models.models if role.startswith("calendar_draft")]
    )
    prepared = flow.prepare()
    assert prepared["status"] == "COMPLETED"
    assert prepared["plan"]["local_preparation_status"] == "READY"
    assert prepared["plan"]["available_grant_modes"] == []
    assert all(
        action["verb"] != "calendar_event_create"
        for action in prepared["plan"]["actions"]
    )
    assert (
        len(
            [
                role
                for role, _ in flow.models.models
                if role.startswith("calendar_draft")
            ]
        )
        == calendar_models
    )
    assert flow.google.calls
    assert all(
        method == "GET" and path.startswith("/gmail/v1/")
        for method, path in flow.google.calls
    )
    assert not flow.google.events
    assert all(call["scopes"] == [GMAIL_READONLY_SCOPE] for call in flow.identity.calls)
    assert (
        len(
            [
                role
                for role, _ in flow.models.models
                if role.startswith("local_artifact")
            ]
        )
        == 2
    )
    flow.assert_no_source_or_credentials_persisted()
    assert not flow.contract_errors, flow.contract_errors


def test_ambiguous_user_correction_cannot_promote_stored_old_calendar_dates(flow):
    flow.seed()
    flow.discover_and_persist()
    prepared = flow.prepare()
    old_hash = prepared["plan"]["hash"]
    flow.models.calendar_abstain = True
    flow.models.local_status = "NEEDS_INPUT"
    flow.api(
        "POST",
        f"/v1/cases/{prepared['case_id']}/messages",
        {
            "text": "예약일을 바꾸고 싶어요. 새 날짜는 아직 정하지 못했어요.",
            "expected_version": prepared["version"],
        },
        template="/v1/cases/{case_id}/messages",
        expected=202,
    )
    flow.case_worker.process(flow.pop("DIRECT_REQUEST_RECEIVED"))
    updated = flow.api(
        "GET", f"/v1/cases/{prepared['case_id']}", template="/v1/cases/{case_id}"
    )
    assert (
        updated["plan"]["hash"] != old_hash
        and updated["plan"]["version"] > prepared["plan"]["version"]
    )
    assert updated["plan"]["available_grant_modes"] == []
    assert updated["status"] == "DECISION_REQUIRED"
    assert updated["plan"]["local_preparation_status"] == "NEEDS_INPUT"
    assert updated["actions"] == []
    assert (
        updated["next_action"] == "What date and time should replace this appointment?"
    )
    assert updated["messages"][-1]["text"] == updated["next_action"]
    assert all(
        action["verb"] != "calendar_event_create"
        and "calendar_event" not in action["parameters"]
        for action in updated["plan"]["actions"]
    )
    assert not flow.google.events
    assert all(
        method == "GET" and path.startswith("/gmail/v1/")
        for method, path in flow.google.calls
    )
    flow.assert_no_source_or_credentials_persisted()
    assert not flow.contract_errors, flow.contract_errors


@pytest.mark.parametrize(
    "long_body,calendar_failure",
    [
        (False, None),
        (True, None),
        (True, "calendar_draft_planner"),
        (True, "calendar_draft_verifier"),
    ],
)
def test_local_ready_content_persists_as_completed_action_without_external_writes(
    flow, long_body, calendar_failure
):
    flow.seed(calendar=calendar_failure is not None)
    flow.models.calendar_invalid_role = calendar_failure
    flow.models.local_artifact_type = "CHECKLIST"
    flow.google.title = "신청서 제출 마감 안내"
    flow.google.body = (
        "신청서는 지정 양식을 사용해 작성해 주세요. 재학증명서도 준비하여 "
        "2030-05-01까지 서류를 제출해 주세요. " + BODY_MARKER
    )
    if long_body:
        flow.google.body += "\n일반 안내 내용입니다. " * 400 + "마지막 추가 안내입니다."
    flow.discover_and_persist()
    flow.models.forbid_duplicate_local_graph = True
    completed = flow.prepare()
    assert completed["status"] == "COMPLETED"
    assert completed["plan"]["local_preparation_status"] == "READY"
    assert completed["plan"]["available_grant_modes"] == []
    assert len(completed["actions"]) == 1
    action = completed["actions"][0]
    assert action["connector"] == "quietpilot" and action["verb"] == "prepare_task"
    assert action["status"] == "SUCCEEDED" and action["verified"] is False
    assert action["parameters"] == {
        "source_ref": REF,
        "title": "Application checklist",
        "artifact_type": "CHECKLIST",
        "content": "- Complete the application using the required form\n- Prepare proof of enrollment\n- Submit the documents by 2030-05-01",
    }
    assert action["result_ref"].startswith("local-preparation:")
    plan_item = flow.db.get_item(
        TableName=TABLE,
        Key={
            "PK": {"S": f"CASE#{completed['case_id']}"},
            "SK": {"S": f"PLAN#{completed['plan']['version']:06d}"},
        },
        ConsistentRead=True,
    )["Item"]
    persisted_plan = json.loads(plan_item["plan_json"]["S"])
    assert (
        persisted_plan["actions"][0]["parameters"]["content"]
        == action["parameters"]["content"]
    )
    assert persisted_plan["local_preparation_status"] == "READY"
    assert "Checklist ready" in completed["next_action"]
    assert not flow.google.events
    assert all(
        method == "GET" and path.startswith("/gmail/v1/")
        for method, path in flow.google.calls
    )
    assert all(call["scopes"] == [GMAIL_READONLY_SCOPE] for call in flow.identity.calls)
    assert any(role.startswith("calendar_draft") for role, _ in flow.models.models) is (
        calendar_failure is not None
    )
    assert [
        role for role, _ in flow.models.models if role.startswith("local_artifact")
    ] == ["local_artifact_writer", "local_artifact_verifier"]
    if long_body:
        for role, model in flow.models.models:
            if role.startswith("local_artifact"):
                prompt = json.loads(model.received_messages[0][0]["content"][0]["text"])
                assert "마지막 추가 안내입니다." in json.dumps(
                    prompt["sources"], ensure_ascii=False
                )
    flow.assert_no_source_or_credentials_persisted()
    assert not flow.contract_errors, flow.contract_errors


def test_full_source_no_action_result_is_committed_without_duplicate_generation(flow):
    flow.seed(calendar=False)
    flow.models.local_artifact_type = "CHECKLIST"
    flow.google.title = "신청서 제출 마감 안내"
    flow.google.body = (
        "신청서는 지정 양식을 사용해 작성해 주세요. 재학증명서도 준비하여 "
        "2030-05-01까지 서류를 제출해 주세요. "
        + BODY_MARKER
        + "\n일반 안내 내용입니다. " * 400
        + "추가 조치가 필요하지 않습니다."
    )
    flow.discover_and_persist()
    flow.models.local_status = "NO_ACTION"
    flow.models.forbid_duplicate_local_graph = True
    completed = flow.prepare()
    assert completed["status"] == "COMPLETED"
    assert completed["plan"]["local_preparation_status"] == "NO_ACTION"
    assert completed["actions"] == [] and not completed["plan"]["available_grant_modes"]
    assert not flow.google.events
    flow.assert_no_source_or_credentials_persisted()
    assert not flow.contract_errors


def test_completed_local_draft_refinement_reaches_the_models_and_keeps_prior_plan(flow):
    flow.seed(calendar=False)
    flow.models.local_artifact_type = "CHECKLIST"
    flow.google.title = "신청서 제출 마감 안내"
    flow.google.body = (
        "신청서는 지정 양식을 사용해 작성해 주세요. 재학증명서도 준비하여 "
        "2030-05-01까지 서류를 제출해 주세요. " + BODY_MARKER
    )
    flow.discover_and_persist()
    completed = flow.prepare()
    original_content = completed["actions"][0]["parameters"]["content"]
    prior_key = {
        "PK": {"S": f"CASE#{completed['case_id']}"},
        "SK": {"S": f"PLAN#{completed['plan']['version']:06d}"},
    }
    prior_plan = flow.db.get_item(TableName=TABLE, Key=prior_key)["Item"]
    accepted = flow.api(
        "POST",
        f"/v1/cases/{completed['case_id']}/messages",
        {"text": "두 번째 항목은 빼 줘.", "expected_version": completed["version"]},
        template="/v1/cases/{case_id}/messages",
        expected=202,
    )
    assert accepted["case"]["status"] == "PREPARING"
    meta = flow.db.get_item(
        TableName=TABLE,
        Key={"PK": prior_key["PK"], "SK": {"S": "META"}},
    )["Item"]
    assert meta["GSI1PK"]["S"] == f"USER#{OWNER}#CASE#ACTIVE"
    flow.models.local_content = "- Complete the application using the required form\n- Submit the documents by 2030-05-01"
    envelope = flow.pop("DIRECT_REQUEST_RECEIVED")
    flow.case_worker.process(envelope)
    updated = flow.api(
        "GET", f"/v1/cases/{completed['case_id']}", template="/v1/cases/{case_id}"
    )
    assert updated["status"] == "COMPLETED"
    assert updated["plan"]["version"] == completed["plan"]["version"] + 1
    assert updated["actions"][0]["parameters"]["content"] == flow.models.local_content
    assert (
        flow.runtime.calls[-1]["previous_local_preparation"]["content"]
        == original_content
    )
    assert len(flow.runtime.calls[-1]["evidence"]) == 2
    local_models = [
        (role, model)
        for role, model in flow.models.models
        if role.startswith("local_artifact")
    ]
    for _, model in local_models[-2:]:
        prompt = json.loads(model.received_messages[0][0]["content"][0]["text"])
        assert prompt["previous_preparation"]["content"] == original_content
        assert [source["kind"] for source in prompt["sources"]] == ["gmail", "direct"]
    assert flow.db.get_item(TableName=TABLE, Key=prior_key)["Item"] == prior_plan
    calls = len(flow.runtime.calls)
    flow.case_worker.process(envelope)
    assert len(flow.runtime.calls) == calls
    assert not flow.google.events
    flow.assert_no_source_or_credentials_persisted()
    assert not flow.contract_errors, flow.contract_errors
