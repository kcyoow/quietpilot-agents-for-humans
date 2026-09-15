from quietpilot_worker.google_maintenance import (
    ConnectedGoogleAccount,
    DynamoMaintenanceStore,
    GoogleMaintenance,
)


class _Store:
    def __init__(self) -> None:
        self.accounts = [
            ConnectedGoogleAccount(
                user_id="user-a",
                history_id="42",
                watch_expiration="1788000000000",
            ),
            ConnectedGoogleAccount(
                user_id="user-b",
                history_id="50",
                watch_expiration="1788000000000",
            ),
        ]
        self.updates: list[tuple[str, str]] = []

    def connected_accounts(self):
        return self.accounts

    def update_watch(self, user_id: str, *, expiration: str) -> None:
        self.updates.append((user_id, expiration))

    def authorization_required(self, user_id: str, epoch: str) -> None:
        self.auth_required = (user_id, epoch)


class _Runtime:
    def __init__(self, results: dict[str, dict[str, object]]) -> None:
        self.results = results
        self.calls: list[tuple[str, str]] = []

    def invoke(self, user_id: str, operation: str, parameters=None):
        del parameters
        self.calls.append((user_id, operation))
        return self.results[user_id]


class _Queue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_history(self, *, user_id: str, history_id: str) -> None:
        self.calls.append((user_id, history_id))


def test_daily_renewal_preserves_cursor_and_queues_only_new_history() -> None:
    store = _Store()
    runtime = _Runtime(
        {
            "user-a": {
                "status": "WATCH_RENEWED",
                "history_id": "45",
                "watch_expiration": "1789000000000",
            },
            "user-b": {
                "status": "WATCH_RENEWED",
                "history_id": "50",
                "watch_expiration": "1789000000000",
            },
        }
    )
    queue = _Queue()

    result = GoogleMaintenance(store, runtime, queue).run("RENEW_GMAIL_WATCHES")

    assert result == {
        "status": "COMPLETED",
        "task": "RENEW_GMAIL_WATCHES",
        "account_count": 2,
        "renewed_count": 2,
        "queued_count": 1,
    }
    assert store.updates == [
        ("user-a", "1789000000000"),
        ("user-b", "1789000000000"),
    ]
    assert queue.calls == [("user-a", "45")]


def test_recovery_sweep_queues_only_ahead_history_without_renewing() -> None:
    store = _Store()
    runtime = _Runtime(
        {
            "user-a": {"status": "HISTORY_HEAD", "history_id": "43"},
            "user-b": {"status": "HISTORY_HEAD", "history_id": "50"},
        }
    )
    queue = _Queue()

    result = GoogleMaintenance(store, runtime, queue).run("RECOVER_CONNECTION_SYNC")

    assert result["queued_count"] == 1
    assert result["renewed_count"] == 0
    assert store.updates == []
    assert queue.calls == [("user-a", "43")]


def test_dynamo_renewal_records_health_timestamp_and_preserves_connected_gate() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def update_item(self, **values: object) -> None:
            self.calls.append(values)

    client = Client()
    DynamoMaintenanceStore("main-table", client).update_watch(
        "user-a", expiration="1789000000000"
    )

    call = client.calls[0]
    expression = str(call["UpdateExpression"])
    values = call["ExpressionAttributeValues"]
    assert "watch_renewed_at=:renewed" in expression
    assert "last_checked_at=:renewed" in expression
    assert "version=if_not_exists(version,:zero)+:one" in expression
    assert call["ConditionExpression"] == "#status=:connected"
    assert str(values[":renewed"]["S"]).endswith("Z")  # type: ignore[index]


def test_expired_google_auth_stops_scheduled_history_and_preserves_account_generation():
    store = _Store()
    store.accounts = [
        ConnectedGoogleAccount("user-a", "42", "1788000000000", "epoch-a")
    ]
    queue = _Queue()
    runtime = _Runtime(
        {
            "user-a": {
                "status": "AUTHORIZATION_REQUIRED",
                "error_code": "GOOGLE_AUTH_REQUIRED",
            }
        }
    )
    result = GoogleMaintenance(store, runtime, queue).run("RECOVER_CONNECTION_SYNC")
    assert store.auth_required == ("user-a", "epoch-a")
    assert result["queued_count"] == 0
    assert not queue.calls and not store.updates
