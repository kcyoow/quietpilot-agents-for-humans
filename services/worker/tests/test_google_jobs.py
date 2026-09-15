import hashlib

import pytest
from quietpilot_worker.google_jobs import (
    MAX_INITIAL_SCAN_PAGES,
    DynamoConnectionWriter,
    GoogleJobProcessor,
    HistorySyncClaim,
    HistorySyncInProgress,
)

SCAN_ID = "a" * 32
PAGE_2_HASH = hashlib.sha256(b"page-2").hexdigest()
PAGE_3_HASH = hashlib.sha256(b"page-3").hexdigest()


class _Runtime:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []
        self.parameters: list[dict[str, object] | None] = []

    def invoke(
        self,
        user_id: str,
        operation: str,
        parameters: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.calls.append((user_id, operation))
        self.parameters.append(parameters)
        return self.result


class _Writer:
    def __init__(
        self,
        cursor: str | None = None,
        *,
        active_scan_id: str | None = SCAN_ID,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.cursor = cursor
        self.sync_calls: list[tuple[str, dict[str, object]]] = []
        self.scan_page_calls: list[tuple[str, dict[str, object]]] = []
        self.release_calls: list[tuple[str, str]] = []
        self.active_scan_id = active_scan_id

    def write(self, user_id: str, **values: object) -> None:
        self.calls.append((user_id, values))

    def get_history_cursor(self, user_id: str) -> str | None:
        return self.cursor

    def is_active_scan(self, user_id: str, *, scan_id: str) -> bool:
        del user_id
        return scan_id == self.active_scan_id

    def claim_history_sync(
        self,
        user_id: str,
        *,
        notified_history_id: str,
    ) -> HistorySyncClaim | None:
        del user_id
        if self.cursor is None or int(notified_history_id) <= int(self.cursor):
            return None
        return HistorySyncClaim(start_history_id=self.cursor, token="sync-token")

    def release_history_sync(self, user_id: str, *, token: str) -> None:
        self.release_calls.append((user_id, token))

    def persist_sync(self, user_id: str, **values: object) -> list[str]:
        self.sync_calls.append((user_id, values))
        return ["candidate-1"]

    def persist_scan_page(self, user_id: str, **values: object) -> list[str]:
        self.scan_page_calls.append((user_id, values))
        return ["candidate-1"] if values.get("candidates") else []


class _ContinuationQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.scan_calls: list[tuple[str, str, str, int, tuple[str, ...], int, int]] = []

    def send_history(self, *, user_id: str, history_id: str) -> None:
        self.calls.append((user_id, history_id))

    def send_scan_page(
        self,
        *,
        user_id: str,
        scan_id: str,
        page_token: str,
        page_number: int,
        page_token_hashes: list[str],
        processed_message_count: int,
        unresolved_evidence_count: int,
    ) -> None:
        self.scan_calls.append(
            (
                user_id,
                scan_id,
                page_token,
                page_number,
                tuple(page_token_hashes),
                processed_message_count,
                unresolved_evidence_count,
            )
        )


def _envelope(
    event_type: str,
    *,
    history_id: str = "45",
    page_token: str = "page-2",
    page_number: int = 2,
    page_token_hashes: list[str] | None = None,
    processed_message_count: int = 1,
    unresolved_evidence_count: int = 0,
) -> dict[str, object]:
    if event_type == "INITIAL_SCAN_REQUESTED":
        payload: dict[str, object] = {"scan_id": SCAN_ID}
    elif event_type == "INITIAL_SCAN_CONTINUATION":
        payload = {
            "scan_id": SCAN_ID,
            "page_token": page_token,
            "page_number": page_number,
            "page_token_hashes": page_token_hashes
            or [hashlib.sha256(page_token.encode()).hexdigest()],
            "processed_message_count": processed_message_count,
            "unresolved_evidence_count": unresolved_evidence_count,
        }
    elif event_type == "GMAIL_HISTORY_AVAILABLE":
        payload = {"history_id": history_id}
    else:
        payload = {}
    return {
        "connector": "google",
        "event_type": event_type,
        "user_id": "cognito-subject",
        "payload": payload,
    }


def test_initial_scan_persists_connected_only_after_watch_confirmation() -> None:
    result = _sync_result()
    result.update(
        {
            "status": "CONNECTED",
            "recent_message_estimate": 12,
            "history_id": "42",
            "watch_expiration": "1788000000000",
            "account_hash": hashlib.sha256(b"private@example.com").hexdigest(),
            "processed_message_count": 1,
            "next_page_token": None,
        }
    )
    result.pop("recovery_mode")
    result.pop("continuation_required")
    runtime = _Runtime(result)
    writer = _Writer()
    queue = _ContinuationQueue()

    GoogleJobProcessor(runtime, writer, queue).process(
        _envelope("INITIAL_SCAN_REQUESTED")
    )

    assert runtime.calls == [("cognito-subject", "GOOGLE_SCAN")]
    assert writer.calls[0][1]["status"] == "CONNECTED"
    assert writer.calls[0][1]["scan_progress"] == 100
    assert writer.calls[0][1]["discovery_revision"] == 0
    assert writer.calls[0][1]["scan_id"] == SCAN_ID
    assert writer.calls[0][1]["details"] == {
        "recent_message_estimate": 12,
        "history_id": "42",
        "watch_expiration": "1788000000000",
        "account_hash": hashlib.sha256(b"private@example.com").hexdigest(),
    }
    assert writer.scan_page_calls[0][1]["complete"] is True
    assert writer.scan_page_calls[0][1]["processed_message_count"] == 1
    assert queue.calls == [("cognito-subject", "45")]


def test_initial_scan_preserves_watch_when_candidate_is_safely_suppressed() -> None:
    result = _sync_result()
    result.update(
        {
            "status": "CONNECTED",
            "recent_message_estimate": 12,
            "history_id": "42",
            "watch_expiration": "1788000000000",
            "account_hash": hashlib.sha256(b"private@example.com").hexdigest(),
            "candidate": None,
            "candidates": [],
            "processed_message_count": 1,
            "next_page_token": None,
        }
    )
    result.pop("recovery_mode")
    result.pop("continuation_required")
    writer = _Writer()
    queue = _ContinuationQueue()

    GoogleJobProcessor(_Runtime(result), writer, queue).process(
        _envelope("INITIAL_SCAN_REQUESTED")
    )

    assert writer.calls[0][1]["status"] == "CONNECTED"
    assert writer.scan_page_calls[0][1]["candidates"] == []
    assert len(writer.scan_page_calls[0][1]["evidence"]) == 1
    assert queue.calls == [("cognito-subject", "45")]


def test_initial_scan_queues_the_next_page_without_certifying_completion() -> None:
    result = _sync_result()
    result.update(
        {
            "status": "CONNECTED",
            "recent_message_estimate": 12,
            "history_id": "42",
            "watch_expiration": "1788000000000",
            "account_hash": hashlib.sha256(b"private@example.com").hexdigest(),
            "next_page_token": "page-2",
            "completion_history_id": None,
        }
    )
    result.pop("recovery_mode")
    result.pop("continuation_required")
    writer = _Writer()
    queue = _ContinuationQueue()

    GoogleJobProcessor(_Runtime(result), writer, queue).process(
        _envelope("INITIAL_SCAN_REQUESTED")
    )

    assert writer.calls[0][1]["scan_progress"] == 8
    assert writer.calls[0][1]["discovery_revision"] == 0
    assert writer.scan_page_calls[0][1]["complete"] is False
    assert queue.scan_calls == [
        ("cognito-subject", SCAN_ID, "page-2", 2, (PAGE_2_HASH,), 1, 0)
    ]


def test_initial_scan_continuation_completes_only_the_matching_scan() -> None:
    result = _sync_result()
    result.update({"status": "SCAN_PAGE", "recent_message_estimate": 9})
    runtime = _Runtime(result)
    writer = _Writer()
    queue = _ContinuationQueue()

    GoogleJobProcessor(runtime, writer, queue).process(
        _envelope("INITIAL_SCAN_CONTINUATION", processed_message_count=8)
    )

    assert runtime.calls == [("cognito-subject", "GOOGLE_SCAN_PAGE")]
    assert runtime.parameters == [{"page_token": "page-2"}]
    assert writer.scan_page_calls[0][1]["scan_id"] == SCAN_ID
    assert writer.scan_page_calls[0][1]["processed_message_count"] == 9
    assert writer.scan_page_calls[0][1]["complete"] is True
    assert queue.calls == [("cognito-subject", "45")]
    assert queue.scan_calls == []


def test_stale_scan_continuation_returns_before_invoking_agentcore() -> None:
    runtime = _Runtime(_sync_result())
    writer = _Writer(active_scan_id="b" * 32)
    queue = _ContinuationQueue()

    GoogleJobProcessor(runtime, writer, queue).process(
        _envelope("INITIAL_SCAN_CONTINUATION", processed_message_count=8)
    )

    assert runtime.calls == []
    assert writer.scan_page_calls == []
    assert queue.scan_calls == []


def test_scan_continuation_carries_unresolved_count_to_the_next_page() -> None:
    result = _sync_result()
    result.update(
        {
            "status": "SCAN_PAGE",
            "recent_message_estimate": 20,
            "next_page_token": "page-3",
            "completion_history_id": None,
            "unresolved_evidence_count": 1,
        }
    )
    writer = _Writer()
    queue = _ContinuationQueue()

    GoogleJobProcessor(_Runtime(result), writer, queue).process(
        _envelope(
            "INITIAL_SCAN_CONTINUATION",
            processed_message_count=8,
            unresolved_evidence_count=2,
        )
    )

    assert writer.scan_page_calls[0][1]["unresolved_evidence_count"] == 3
    assert queue.scan_calls == [
        (
            "cognito-subject",
            SCAN_ID,
            "page-3",
            3,
            (PAGE_2_HASH, PAGE_3_HASH),
            9,
            3,
        )
    ]


def test_scan_continuation_rejects_a_page_token_cycle_before_persisting() -> None:
    result = _sync_result()
    result.update(
        {
            "status": "SCAN_PAGE",
            "recent_message_estimate": 20,
            "next_page_token": "page-2",
            "completion_history_id": None,
        }
    )
    writer = _Writer()
    queue = _ContinuationQueue()

    with pytest.raises(RuntimeError, match="page token cycle"):
        GoogleJobProcessor(_Runtime(result), writer, queue).process(
            _envelope("INITIAL_SCAN_CONTINUATION")
        )

    assert writer.scan_page_calls == []
    assert queue.scan_calls == []


def test_scan_continuation_rejects_more_than_the_bounded_page_limit() -> None:
    result = _sync_result()
    result.update(
        {
            "status": "SCAN_PAGE",
            "recent_message_estimate": 1000,
            "next_page_token": "page-3",
            "completion_history_id": None,
        }
    )
    writer = _Writer()
    queue = _ContinuationQueue()

    with pytest.raises(RuntimeError, match="page limit"):
        GoogleJobProcessor(_Runtime(result), writer, queue).process(
            _envelope(
                "INITIAL_SCAN_CONTINUATION",
                page_number=MAX_INITIAL_SCAN_PAGES,
            )
        )

    assert writer.scan_page_calls == []
    assert queue.scan_calls == []


def test_scan_continuation_accepts_one_legacy_message_without_lineage() -> None:
    result = _sync_result()
    result.update(
        {
            "status": "SCAN_PAGE",
            "recent_message_estimate": 20,
            "next_page_token": "page-3",
            "completion_history_id": None,
        }
    )
    envelope = _envelope(
        "INITIAL_SCAN_CONTINUATION",
        processed_message_count=8,
    )
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    payload.pop("page_number")
    payload.pop("page_token_hashes")
    writer = _Writer()
    queue = _ContinuationQueue()

    GoogleJobProcessor(_Runtime(result), writer, queue).process(envelope)

    assert queue.scan_calls == [
        (
            "cognito-subject",
            SCAN_ID,
            "page-3",
            3,
            (PAGE_2_HASH, PAGE_3_HASH),
            9,
            0,
        )
    ]


def test_revoke_persists_disconnected_only_after_runtime_confirmation() -> None:
    runtime = _Runtime({"status": "DISCONNECTED"})
    writer = _Writer()

    GoogleJobProcessor(runtime, writer).process(_envelope("GOOGLE_CONNECTION_REVOKED"))

    assert runtime.calls == [("cognito-subject", "GOOGLE_DISCONNECT")]
    assert writer.calls[0][1] == {
        "status": "DISCONNECTED",
        "granted_scopes": [],
        "scan_progress": 0,
        "discovery_revision": 0,
    }


def _sync_result(*, continuation_required: bool = False) -> dict[str, object]:
    evidence_ref = f"gmail:{hashlib.sha256(b'message-1').hexdigest()}"
    return {
        "status": "SYNCED",
        "history_id": "44" if continuation_required else "45",
        "recovery_mode": "INCREMENTAL",
        "continuation_required": continuation_required,
        "discovery_validated": True,
        "unresolved_evidence_count": 0,
        "processed_message_count": 1,
        "next_page_token": None,
        "completion_history_id": "45",
        "recent_message_estimate": 1,
        "evidence": [
            {
                "ref": evidence_ref,
                "revision": 1,
                "source": "gmail",
                "title": "Project review on Friday",
                "facts": [
                    "received_at_unix_ms=1788000000000",
                    "sender_domain=example.com",
                ],
                "untrusted_text": None,
            }
        ],
        "candidates": [
            {
                "outcome": "마감 전에 끝낼 일을 준비",
                "summary": "과제 제출 메일에서 지금 준비할 후속 작업을 찾았어요.",
                "why_now": "기한을 놓치기 전에 준비할 수 있어요.",
                "opportunity_type": "DEADLINE",
                "evidence_refs": [evidence_ref],
                "confidence": 0.86,
                "uncertainty_reason": None,
                "primary_group_hint": "deadlines",
                "tags": ["deadline"],
                "risk": "LOW",
                "required_capabilities": ["quietpilot.task.prepare"],
                "proposed_actions": [
                    {
                        "connector": "quietpilot",
                        "target_resource": "case:deadline",
                        "verb": "prepare_task",
                        "parameters": {
                            "source_ref": evidence_ref,
                            "title": "Assignment deadline Friday",
                        },
                        "required_scopes": [],
                        "risk": "LOW",
                        "reversible": True,
                        "verification_method": "case_plan_readback",
                    }
                ],
                "fingerprint_inputs": [
                    evidence_ref,
                    "DEADLINE",
                    "quietpilot.prepare_task",
                ],
            }
        ],
    }


def test_history_event_uses_stored_cursor_and_persists_one_grounded_candidate() -> None:
    runtime = _Runtime(_sync_result())
    writer = _Writer(cursor="42")
    queue = _ContinuationQueue()

    GoogleJobProcessor(runtime, writer, queue).process(
        _envelope("GMAIL_HISTORY_AVAILABLE")
    )

    assert runtime.calls == [("cognito-subject", "GOOGLE_HISTORY_SYNC")]
    assert runtime.parameters == [{"start_history_id": "42"}]
    assert len(writer.sync_calls) == 1
    user_id, values = writer.sync_calls[0]
    assert user_id == "cognito-subject"
    assert values["start_history_id"] == "42"
    assert values["history_id"] == "45"
    assert values["sync_token"] == "sync-token"
    assert values["candidates"][0]["evidence_refs"] == [  # type: ignore[index]
        values["evidence"][0]["ref"]  # type: ignore[index]
    ]
    assert queue.calls == []


def test_history_event_schedules_continuation_only_after_persisting_progress() -> None:
    runtime = _Runtime(_sync_result(continuation_required=True))
    writer = _Writer(cursor="42")
    queue = _ContinuationQueue()

    GoogleJobProcessor(runtime, writer, queue).process(
        _envelope("GMAIL_HISTORY_AVAILABLE", history_id="50")
    )

    assert writer.sync_calls[0][1]["history_id"] == "44"
    assert queue.calls == [("cognito-subject", "50")]


def test_stale_or_disconnected_history_event_does_not_call_agentcore() -> None:
    for cursor, notified in [(None, "45"), ("45", "45"), ("46", "45")]:
        runtime = _Runtime(_sync_result())
        writer = _Writer(cursor=cursor)
        GoogleJobProcessor(runtime, writer).process(
            _envelope("GMAIL_HISTORY_AVAILABLE", history_id=notified)
        )
        assert runtime.calls == []
        assert writer.sync_calls == []


def test_history_sync_failure_releases_only_the_owned_lease() -> None:
    class _FailingRuntime(_Runtime):
        def invoke(
            self,
            user_id: str,
            operation: str,
            parameters: dict[str, object] | None = None,
        ) -> dict[str, object]:
            super().invoke(user_id, operation, parameters)
            raise RuntimeError("controlled AgentCore failure")

    runtime = _FailingRuntime(_sync_result())
    writer = _Writer(cursor="42")

    with pytest.raises(RuntimeError, match="controlled AgentCore failure"):
        GoogleJobProcessor(runtime, writer).process(
            _envelope("GMAIL_HISTORY_AVAILABLE")
        )

    assert runtime.calls == [("cognito-subject", "GOOGLE_HISTORY_SYNC")]
    assert writer.release_calls == [("cognito-subject", "sync-token")]


class _Dynamo:
    def __init__(self) -> None:
        self.transactions: list[list[dict[str, object]]] = []

    def transact_write_items(self, *, TransactItems):
        self.transactions.append(TransactItems)


class _UpdateDynamo:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def update_item(self, **values: object) -> None:
        self.updates.append(values)


class ConditionalCheckFailedException(Exception):
    pass


class _Exceptions:
    ConditionalCheckFailedException = ConditionalCheckFailedException


class _LeaseDynamo:
    exceptions = _Exceptions()

    def __init__(self, *, cursor: str = "42") -> None:
        self.item: dict[str, dict[str, str]] = {
            "status": {"S": "CONNECTED"},
            "gmail_history_id": {"S": cursor},
        }

    def get_item(self, **kwargs):
        del kwargs
        return {"Item": dict(self.item)}

    def update_item(
        self,
        *,
        UpdateExpression,
        ConditionExpression,
        ExpressionAttributeValues,
        **kwargs,
    ) -> None:
        del kwargs
        values = ExpressionAttributeValues
        if UpdateExpression.startswith("SET gmail_sync_token"):
            now = int(values[":now"]["N"])
            active_expiry = int(self.item.get("gmail_sync_expires_at", {"N": "0"})["N"])
            if (
                self.item["status"]["S"] != values[":connected"]["S"]
                or self.item["gmail_history_id"]["S"] != values[":start"]["S"]
                or ("gmail_sync_token" in self.item and active_expiry > now)
            ):
                raise ConditionalCheckFailedException
            self.item["gmail_sync_token"] = dict(values[":token"])
            self.item["gmail_sync_expires_at"] = dict(values[":expiry"])
            return

        assert UpdateExpression.startswith("REMOVE gmail_sync_token")
        assert ConditionExpression == "gmail_sync_token=:token"
        if self.item.get("gmail_sync_token") != values[":token"]:
            raise ConditionalCheckFailedException
        self.item.pop("gmail_sync_token", None)
        self.item.pop("gmail_sync_expires_at", None)


def test_dynamo_sync_is_one_cursor_guarded_transaction_with_stable_candidate() -> None:
    dynamo = _Dynamo()
    writer = DynamoConnectionWriter("main-table", dynamo)
    result = _sync_result()

    candidate_ids = writer.persist_sync(
        "cognito-subject",
        start_history_id="42",
        history_id="45",
        recovery_mode="INCREMENTAL",
        evidence=result["evidence"],  # type: ignore[arg-type]
        candidates=result["candidates"],  # type: ignore[arg-type]
    )

    assert len(candidate_ids) == 1 and len(candidate_ids[0]) == 32
    candidate_id = candidate_ids[0]
    assert len(dynamo.transactions) == 1
    transaction = dynamo.transactions[0]
    assert len(transaction) == 3
    serialized = str(transaction)
    assert "EVIDENCE#gmail:" in serialized
    assert f"CANDIDATE#{candidate_id}" in serialized
    connection_update = transaction[-1]["Update"]
    assert connection_update["ConditionExpression"] == (
        "#status=:connected AND gmail_history_id=:start"
    )
    assert connection_update["ExpressionAttributeValues"][":discovery_revision"] == {
        "N": "1"
    }
    assert "person@example.com" not in serialized


def test_initial_connection_write_records_watch_renewal_and_sync_mode() -> None:
    dynamo = _UpdateDynamo()
    writer = DynamoConnectionWriter("main-table", dynamo)

    writer.write(
        "cognito-subject",
        status="CONNECTED",
        granted_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        scan_progress=8,
        discovery_revision=0,
        details={
            "recent_message_estimate": 1,
            "history_id": "42",
            "watch_expiration": "1788000000000",
            "account_hash": hashlib.sha256(b"private@example.com").hexdigest(),
        },
        scan_id=SCAN_ID,
    )

    update = dynamo.updates[0]
    expression = str(update["UpdateExpression"])
    values = update["ExpressionAttributeValues"]
    assert "watch_renewed_at=:renewed" in expression
    assert "last_sync_mode=:sync_mode" in expression
    assert "discovery_revision=:discovery_revision" in expression
    assert "gmail_scan_id=:scan_id" in expression
    assert values[":discovery_revision"] == {"N": "0"}  # type: ignore[index]
    assert values[":scan_id"] == {"S": SCAN_ID}  # type: ignore[index]
    assert values[":sync_mode"] == {"S": "INITIAL_7_DAY"}  # type: ignore[index]


def test_scan_page_certifies_revision_only_after_the_last_page() -> None:
    dynamo = _Dynamo()
    writer = DynamoConnectionWriter("main-table", dynamo)
    result = _sync_result()

    candidate_ids = writer.persist_scan_page(
        "cognito-subject",
        scan_id=SCAN_ID,
        processed_message_count=9,
        recent_message_estimate=9,
        unresolved_evidence_count=0,
        evidence=result["evidence"],  # type: ignore[arg-type]
        candidates=result["candidates"],  # type: ignore[arg-type]
        complete=True,
    )

    assert len(candidate_ids) == 1
    connection_update = dynamo.transactions[0][-1]["Update"]
    assert connection_update["ConditionExpression"] == (
        "#status=:connected AND gmail_scan_id=:scan_id"
    )
    assert "discovery_revision=:revision" in connection_update["UpdateExpression"]
    assert (
        "REMOVE gmail_scan_id, gmail_scan_unresolved_count"
        in connection_update["UpdateExpression"]
    )
    assert connection_update["ExpressionAttributeValues"][":revision"] == {"N": "1"}


def test_scan_page_marks_incomplete_analysis_as_error_instead_of_no_work() -> None:
    dynamo = _Dynamo()
    writer = DynamoConnectionWriter("main-table", dynamo)
    result = _sync_result()

    writer.persist_scan_page(
        "cognito-subject",
        scan_id=SCAN_ID,
        processed_message_count=9,
        recent_message_estimate=9,
        unresolved_evidence_count=1,
        evidence=result["evidence"],  # type: ignore[arg-type]
        candidates=[],
        complete=True,
    )

    connection_update = dynamo.transactions[0][-1]["Update"]
    values = connection_update["ExpressionAttributeValues"]
    assert values[":progress"] == {"N": "99"}
    assert values[":final_status"] == {"S": "ERROR"}
    assert values[":revision"] == {"N": "0"}
    assert values[":error"] == {"S": "DISCOVERY_INCOMPLETE"}


def test_dynamo_history_claim_allows_only_one_active_user_sync() -> None:
    dynamo = _LeaseDynamo()
    first = DynamoConnectionWriter(
        "main-table",
        dynamo,
        clock=lambda: 1_000,
        token_factory=lambda: "lease-one",
    )
    second = DynamoConnectionWriter(
        "main-table",
        dynamo,
        clock=lambda: 1_000,
        token_factory=lambda: "lease-two",
    )

    claim = first.claim_history_sync(
        "cognito-subject",
        notified_history_id="45",
    )
    with pytest.raises(HistorySyncInProgress, match="in progress"):
        second.claim_history_sync(
            "cognito-subject",
            notified_history_id="46",
        )

    assert claim == HistorySyncClaim(start_history_id="42", token="lease-one")
    first.release_history_sync("cognito-subject", token="lease-one")
    assert second.claim_history_sync(
        "cognito-subject",
        notified_history_id="46",
    ) == HistorySyncClaim(start_history_id="42", token="lease-two")


def test_dynamo_history_claim_can_recover_an_expired_lease() -> None:
    now = [1_000.0]
    dynamo = _LeaseDynamo()
    first = DynamoConnectionWriter(
        "main-table",
        dynamo,
        clock=lambda: now[0],
        token_factory=lambda: "lease-one",
    )
    second = DynamoConnectionWriter(
        "main-table",
        dynamo,
        clock=lambda: now[0],
        token_factory=lambda: "lease-two",
    )
    assert (
        first.claim_history_sync(
            "cognito-subject",
            notified_history_id="45",
        )
        is not None
    )

    now[0] = 1_151.0
    assert second.claim_history_sync(
        "cognito-subject",
        notified_history_id="46",
    ) == HistorySyncClaim(start_history_id="42", token="lease-two")


def test_dynamo_history_persist_requires_and_clears_owned_lease() -> None:
    dynamo = _Dynamo()
    writer = DynamoConnectionWriter("main-table", dynamo)
    result = _sync_result()

    writer.persist_sync(
        "cognito-subject",
        start_history_id="42",
        history_id="45",
        recovery_mode="INCREMENTAL",
        evidence=result["evidence"],  # type: ignore[arg-type]
        candidates=result["candidates"],  # type: ignore[arg-type]
        sync_token="lease-one",
    )

    connection_update = dynamo.transactions[0][-1]["Update"]
    assert connection_update["ConditionExpression"] == (
        "#status=:connected AND gmail_history_id=:start "
        "AND gmail_sync_token=:sync_token"
    )
    assert connection_update["UpdateExpression"].endswith(
        "REMOVE gmail_sync_token, gmail_sync_expires_at"
    )
    assert connection_update["ExpressionAttributeValues"][":sync_token"] == {
        "S": "lease-one"
    }
