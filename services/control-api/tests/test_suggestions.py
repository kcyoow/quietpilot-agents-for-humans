import json

from quietpilot_control_api.suggestions import (
    DynamoSuggestionStore,
    SuggestionService,
)


def _candidate_item(index: int) -> dict[str, object]:
    return {
        "PK": {"S": "USER#cognito-subject"},
        "SK": {"S": f"CANDIDATE#candidate-{index:03d}"},
        "candidate_id": {"S": f"candidate-{index}"},
        "outcome": {"S": f"후보 결과 {index}"},
        "summary": {"S": "확인할 작업을 찾았어요."},
        "why_now": {"S": "마감 전에 준비할 수 있어요."},
        "opportunity_type": {"S": "DEADLINE"},
        "primary_group_id": {"S": f"group-{index % 7}"},
        "risk": {"S": "LOW"},
        "status": {"S": "VISIBLE"},
        "confidence": {"N": "0.9"},
        "version": {"N": "1"},
        "mail_profile_version": {"N": "1"},
        "mail_scan_id": {"S": "a" * 32},
        "created_at": {"S": "2026-08-28T00:00:00Z"},
        "updated_at": {"S": "2026-08-28T00:00:00Z"},
        "evidence_refs": {"L": [{"S": f"gmail:{index}"}]},
        "tags": {"L": [{"S": "gmail"}]},
        "required_capabilities": {"L": [{"S": "quietpilot.task.prepare"}]},
        "proposed_actions_json": {
            "S": json.dumps(
                [
                    {
                        "connector": "quietpilot",
                        "target_resource": "case:deadline",
                        "verb": "prepare_task",
                        "parameters": {"source_ref": f"gmail:{index}"},
                        "required_scopes": [],
                        "risk": "LOW",
                        "reversible": True,
                        "verification_method": "case_plan_readback",
                    }
                ]
            )
        },
    }


class _MailStateClient:
    def get_item(self, **request: object) -> dict[str, object]:
        if request["Key"]["SK"]["S"] == "CONNECTION#google":
            return {"Item": {"status": {"S": "CONNECTED"}}}
        return {
            "Item": {
                "profile_json": {
                    "S": json.dumps(
                        {
                            "tags": ["학교"],
                            "description": "",
                            "version": 1,
                            "updated_at": None,
                        }
                    )
                },
                "scan_json": {
                    "S": json.dumps(
                        {"scan_id": "a" * 32, "status": "READY", "profile_version": 1}
                    )
                },
            }
        }


class _Store:
    def __init__(self) -> None:
        self.user_ids: list[str] = []

    def list_visible(self, user_id: str) -> list[dict[str, object]]:
        self.user_ids.append(user_id)
        return [
            {
                "candidate_id": "candidate-1",
                "provider": "google",
                "source_type": "CONNECTED_SIGNAL",
                "outcome": "Gmail 검토: Project review on Friday",
                "summary": "확인할 작업을 찾았어요.",
                "why_now": "마감 전에 준비할 수 있어요.",
                "opportunity_type": "DEADLINE",
                "evidence_refs": ["gmail:one"],
                "confidence": 0.9,
                "risk": "LOW",
                "primary_group_id": "gmail-mail-schedule",
                "tags": ["gmail"],
                "proposed_actions": [
                    {
                        "connector": "quietpilot",
                        "target_resource": "case:deadline",
                        "verb": "prepare_task",
                        "parameters": {"source_ref": "gmail:one"},
                        "required_scopes": [],
                        "risk": "LOW",
                        "reversible": True,
                        "verification_method": "case_plan_readback",
                    }
                ],
                "status": "VISIBLE",
                "version": 1,
                "created_at": "2026-08-28T00:00:00Z",
                "updated_at": "2026-08-28T00:00:00Z",
            }
        ]

    def hide_once(
        self, user_id: str, candidate_id: str, expected_version: int
    ) -> dict[str, object]:
        assert (user_id, candidate_id, expected_version) == (
            "cognito-subject",
            "candidate-1",
            1,
        )
        return {"candidate_id": candidate_id, "status": "HIDDEN"}

    def reduce_similar(
        self, user_id: str, candidate_id: str, expected_version: int
    ) -> dict[str, object]:
        assert (user_id, candidate_id, expected_version) == (
            "cognito-subject",
            "candidate-1",
            1,
        )
        return {
            "mode": "REDUCE_SIMILAR",
            "candidate": None,
            "affected_candidate_ids": ["candidate-2"],
            "rule_id": "rule-1",
        }

    def undo_suppression(self, user_id: str, rule_id: str) -> list[str]:
        assert (user_id, rule_id) == ("cognito-subject", "rule-1")
        return ["candidate-2"]


def test_lists_owner_bound_candidate_and_derived_group() -> None:
    store = _Store()
    service = SuggestionService(store)

    suggestions = service.list_suggestions("cognito-subject")
    groups = service.list_groups("cognito-subject")

    assert suggestions["suggestions"][0]["candidate_id"] == "candidate-1"
    assert groups == {
        "groups": [
            {
                "group_id": "gmail-mail-schedule",
                "label": "Tasks to prepare",
                "reason": "Related tasks, grouped together.",
                "candidate_count": 1,
                "highest_risk": "LOW",
            }
        ],
        "next_cursor": None,
    }
    assert store.user_ids == ["cognito-subject", "cognito-subject"]


def test_applies_distinct_hide_and_reversible_reduce_similar_feedback() -> None:
    service = SuggestionService(_Store())

    hidden = service.submit_feedback(
        "cognito-subject",
        candidate_id="candidate-1",
        mode="HIDE_ONCE",
        expected_version=1,
    )
    reduced = service.submit_feedback(
        "cognito-subject",
        candidate_id="candidate-1",
        mode="REDUCE_SIMILAR",
        expected_version=1,
    )
    restored = service.undo_suppression("cognito-subject", "rule-1")

    assert hidden["affected_candidate_ids"] == ["candidate-1"]
    assert hidden["rule_id"] is None
    assert reduced["affected_candidate_ids"] == ["candidate-2"]
    assert restored == {"restored_candidate_ids": ["candidate-2"]}


def test_dynamo_store_reads_more_than_one_hundred_candidates_across_pages() -> None:
    class Client(_MailStateClient):
        def __init__(self) -> None:
            self.candidate_queries: list[dict[str, object]] = []

        def query(self, **request: object) -> dict[str, object]:
            values = request["ExpressionAttributeValues"]
            assert isinstance(values, dict)
            if ":suppression" in values:
                return {"Items": [], "ScannedCount": 0}
            self.candidate_queries.append(request)
            if "ExclusiveStartKey" not in request:
                return {
                    "Items": [_candidate_item(index) for index in range(1, 101)],
                    "ScannedCount": 100,
                    "LastEvaluatedKey": {
                        "PK": {"S": "USER#cognito-subject"},
                        "SK": {"S": "CANDIDATE#candidate-100"},
                    },
                }
            return {
                "Items": [_candidate_item(index) for index in range(101, 145)],
                "ScannedCount": 44,
            }

    client = Client()
    visible = DynamoSuggestionStore("table", client).list_visible("cognito-subject")

    assert len(visible) == 144
    assert visible[-1]["candidate_id"] == "candidate-144"
    assert len(client.candidate_queries) == 2
    assert client.candidate_queries[1]["ExclusiveStartKey"] == {
        "PK": {"S": "USER#cognito-subject"},
        "SK": {"S": "CANDIDATE#candidate-100"},
    }


def test_active_suppression_keeps_its_source_candidate_visible_for_undo() -> None:
    class Client(_MailStateClient):
        def query(self, **request: object) -> dict[str, object]:
            values = request["ExpressionAttributeValues"]
            assert isinstance(values, dict)
            if ":suppression" in values:
                return {
                    "Items": [
                        {
                            "group_id": {"S": "group-1"},
                            "source_candidate_id": {"S": "candidate-1"},
                        }
                    ]
                }
            first = _candidate_item(1)
            second = _candidate_item(8)
            first["primary_group_id"] = {"S": "group-1"}
            second["primary_group_id"] = {"S": "group-1"}
            return {"Items": [first, second], "ScannedCount": 2}

    visible = DynamoSuggestionStore("table", Client()).list_visible("cognito-subject")

    assert [candidate["candidate_id"] for candidate in visible] == ["candidate-1"]


def test_personalized_candidate_wire_carries_exact_profile_and_scan_generation() -> (
    None
):
    class Client(_MailStateClient):
        def query(self, **request):
            if ":suppression" in request["ExpressionAttributeValues"]:
                return {"Items": []}
            return {"Items": [_candidate_item(1)]}

    candidate = DynamoSuggestionStore("table", Client()).list_visible(
        "cognito-subject"
    )[0]
    assert candidate["mail_profile_version"] == 1
    assert candidate["mail_scan_id"] == "a" * 32


def test_reconnect_hides_old_google_candidates_but_preserves_other_providers() -> None:
    class Client(_MailStateClient):
        def get_item(self, **request):
            value = super().get_item(**request)
            if request["Key"]["SK"]["S"] == "CONNECTION#google":
                value["Item"]["mail_connection_id"] = {"S": "new-connection"}
            return value

        def query(self, **request):
            if ":suppression" in request["ExpressionAttributeValues"]:
                return {"Items": []}
            other = _candidate_item(2)
            other["provider"] = {"S": "smartthings"}
            return {"Items": [_candidate_item(1), other]}

    candidates = DynamoSuggestionStore("table", Client()).list_visible(
        "cognito-subject"
    )
    assert len(candidates) == 1 and candidates[0]["provider"] == "smartthings"
