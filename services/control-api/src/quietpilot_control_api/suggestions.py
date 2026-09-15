"""Owner-bound read model for live Candidate summaries."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

MAX_VISIBLE_CANDIDATES = 500
MAX_EVALUATED_CANDIDATES = 2_000
CANDIDATE_PAGE_SIZE = 100
MAX_CANDIDATE_QUERY_PAGES = 40
RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
GROUP_COPY = {
    "appointments": ("Appointments", "Preparation for appointments and changes."),
    "deadlines": ("Deadlines", "Preparation for deadlines."),
    "follow-ups": ("Replies and follow-ups", "Requests that need a response."),
}


class SuggestionStore(Protocol):
    def list_visible(self, user_id: str) -> list[dict[str, object]]: ...

    def hide_once(
        self, user_id: str, candidate_id: str, expected_version: int
    ) -> dict[str, object]: ...

    def reduce_similar(
        self, user_id: str, candidate_id: str, expected_version: int
    ) -> dict[str, object]: ...

    def undo_suppression(self, user_id: str, rule_id: str) -> list[str]: ...


class SuggestionInputError(ValueError):
    pass


class SuggestionNotFound(LookupError):
    pass


class SuggestionConflict(RuntimeError):
    pass


class SuggestionService:
    def __init__(self, store: SuggestionStore) -> None:
        self._store = store

    def list_suggestions(self, user_id: str) -> dict[str, object]:
        return {"suggestions": self._store.list_visible(user_id), "next_cursor": None}

    def list_groups(self, user_id: str) -> dict[str, object]:
        candidates = self._store.list_visible(user_id)
        grouped: dict[str, list[dict[str, object]]] = {}
        for candidate in candidates:
            grouped.setdefault(str(candidate["primary_group_id"]), []).append(candidate)
        groups = []
        for group_id, members in grouped.items():
            highest_risk = max(
                (str(member["risk"]) for member in members),
                key=lambda risk: RISK_ORDER[risk],
            )
            groups.append(
                {
                    "group_id": group_id,
                    "label": GROUP_COPY.get(
                        group_id,
                        ("Tasks to prepare", "Related tasks, grouped together."),
                    )[0],
                    "reason": GROUP_COPY.get(
                        group_id,
                        ("Tasks to prepare", "Related tasks, grouped together."),
                    )[1],
                    "candidate_count": len(members),
                    "highest_risk": highest_risk,
                }
            )
        return {"groups": groups, "next_cursor": None}

    def submit_feedback(
        self,
        user_id: str,
        *,
        candidate_id: object,
        mode: object,
        expected_version: object,
    ) -> dict[str, object]:
        valid_candidate_id = _identifier(candidate_id, "candidate_id")
        version = _version(expected_version)
        if mode == "HIDE_ONCE":
            candidate = self._store.hide_once(user_id, valid_candidate_id, version)
            return {
                "mode": mode,
                "candidate": candidate,
                "affected_candidate_ids": [valid_candidate_id],
                "rule_id": None,
            }
        if mode == "REDUCE_SIMILAR":
            return self._store.reduce_similar(user_id, valid_candidate_id, version)
        if mode == "ADJUST_SCOPE":
            return {
                "mode": mode,
                "candidate": None,
                "affected_candidate_ids": [],
                "rule_id": None,
                "preview": "Narrowing a connection never grants more permission.",
            }
        raise SuggestionInputError("Suggestion feedback mode is invalid")

    def undo_suppression(self, user_id: str, rule_id: object) -> dict[str, object]:
        valid_rule_id = _identifier(rule_id, "rule_id")
        return {
            "restored_candidate_ids": self._store.undo_suppression(
                user_id, valid_rule_id
            )
        }


class DynamoSuggestionStore:
    def __init__(self, table_name: str, client: Any) -> None:
        self._table_name = table_name
        self._client = client

    @classmethod
    def from_environment(cls) -> DynamoSuggestionStore:
        import boto3

        return cls(_required_environment("MAIN_TABLE_NAME"), boto3.client("dynamodb"))

    def list_visible(self, user_id: str) -> list[dict[str, object]]:
        from .mail import DynamoMailStore, configured

        mail = DynamoMailStore(self._table_name, self._client)
        mail_state = mail.public_state(user_id)
        profile = mail_state["profile"]
        scan_id = mail_state["scan"]["scan_id"]
        connection = mail.connection(user_id)
        connected = connection.get("status", {}).get("S") == "CONNECTED"
        epoch = _string(connection, "mail_connection_id") or ""
        mail_version = profile["version"] if connected and configured(profile) else None
        candidates = self._list_candidate_items(user_id)
        suppressions = self._active_suppressions(user_id)
        visible: list[dict[str, object]] = []
        for item in candidates:
            if (_string(item, "provider") or "google") == "google" and (
                mail_version is None
                or _integer(item, "mail_profile_version") != mail_version
                or scan_id is None
                or _string(item, "mail_scan_id") != scan_id
                or (_string(item, "mail_connection_id") or "") != epoch
            ):
                continue
            group_id = _string(item, "primary_group_id")
            if (
                group_id in suppressions
                and _string(item, "candidate_id") != suppressions[group_id]
            ):
                continue
            candidate = _candidate_from_item(item)
            # Records created by the superseded inbox-as-candidate pipeline are
            # intentionally not user-visible because they contain no grounded action.
            if candidate["proposed_actions"]:
                visible.append(candidate)
        current = mail.public_state(user_id)
        if (
            current["profile"]["version"] != profile["version"]
            or current["scan"]["scan_id"] != scan_id
        ):
            raise SuggestionConflict("Mail interests changed")
        return visible

    def hide_once(
        self, user_id: str, candidate_id: str, expected_version: int
    ) -> dict[str, object]:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            response = self._client.update_item(
                TableName=self._table_name,
                Key=_candidate_key(user_id, candidate_id),
                UpdateExpression=(
                    "SET #status=:hidden, version=version+:one, updated_at=:now "
                    "REMOVE GSI1PK, GSI1SK"
                ),
                ConditionExpression="#status=:visible AND version=:version",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":hidden": {"S": "HIDDEN"},
                    ":visible": {"S": "VISIBLE"},
                    ":version": {"N": str(expected_version)},
                    ":one": {"N": "1"},
                    ":now": {"S": now},
                },
                ReturnValues="ALL_NEW",
            )
        except Exception as error:
            if _error_code(error) == "ConditionalCheckFailedException":
                raise SuggestionConflict(
                    "Suggestion changed before feedback"
                ) from error
            raise
        attributes = response.get("Attributes")
        if not isinstance(attributes, Mapping):
            raise TypeError("Hidden Candidate response is invalid")
        return _candidate_from_item(attributes, expected_status="HIDDEN")

    def reduce_similar(
        self, user_id: str, candidate_id: str, expected_version: int
    ) -> dict[str, object]:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=_candidate_key(user_id, candidate_id),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if (
            not isinstance(item, Mapping)
            or _string(item, "status") != "VISIBLE"
            or _integer(item, "version") != expected_version
        ):
            raise SuggestionConflict("Suggestion changed before feedback")
        group_id = _string(item, "primary_group_id")
        if group_id is None:
            raise TypeError("Suggestion group is invalid")
        rule_id = hashlib.sha256(f"{user_id}:{group_id}".encode()).hexdigest()[:24]
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._client.update_item(
            TableName=self._table_name,
            Key={
                "PK": {"S": f"USER#{user_id}"},
                "SK": {"S": f"SUPPRESSION#{rule_id}"},
            },
            UpdateExpression=(
                "SET entity_type=:entity, rule_id=:rule, user_id=:user, "
                "group_id=:group, active=:active, created_at=if_not_exists(created_at,:now), "
                "source_candidate_id=:source, "
                "updated_at=:now, version=if_not_exists(version,:zero)+:one"
            ),
            ExpressionAttributeValues={
                ":entity": {"S": "suggestion_suppression"},
                ":rule": {"S": rule_id},
                ":user": {"S": user_id},
                ":group": {"S": group_id},
                ":source": {"S": candidate_id},
                ":active": {"BOOL": True},
                ":now": {"S": now},
                ":zero": {"N": "0"},
                ":one": {"N": "1"},
            },
        )
        affected = [
            str(candidate["candidate_id"])
            for candidate in (
                _candidate_from_item(candidate)
                for candidate in self._list_candidate_items(user_id)
            )
            if candidate["primary_group_id"] == group_id
            and candidate["candidate_id"] != candidate_id
        ]
        return {
            "mode": "REDUCE_SIMILAR",
            "candidate": None,
            "affected_candidate_ids": affected,
            "rule_id": rule_id,
        }

    def undo_suppression(self, user_id: str, rule_id: str) -> list[str]:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            response = self._client.update_item(
                TableName=self._table_name,
                Key={
                    "PK": {"S": f"USER#{user_id}"},
                    "SK": {"S": f"SUPPRESSION#{rule_id}"},
                },
                UpdateExpression=(
                    "SET active=:inactive, updated_at=:now, version=version+:one"
                ),
                ConditionExpression="user_id=:user AND active=:active",
                ExpressionAttributeValues={
                    ":inactive": {"BOOL": False},
                    ":active": {"BOOL": True},
                    ":user": {"S": user_id},
                    ":now": {"S": now},
                    ":one": {"N": "1"},
                },
                ReturnValues="ALL_OLD",
            )
        except Exception as error:
            if _error_code(error) == "ConditionalCheckFailedException":
                raise SuggestionNotFound("Suppression rule was not found") from error
            raise
        attributes = response.get("Attributes")
        if not isinstance(attributes, Mapping):
            raise TypeError("Suppression response is invalid")
        group_id = _string(attributes, "group_id")
        source_candidate_id = _string(attributes, "source_candidate_id")
        if group_id is None or source_candidate_id is None:
            raise TypeError("Suppression group is invalid")
        return [
            str(candidate["candidate_id"])
            for candidate in (
                _candidate_from_item(candidate)
                for candidate in self._list_candidate_items(user_id)
            )
            if candidate["primary_group_id"] == group_id
            and candidate["candidate_id"] != source_candidate_id
        ]

    def _list_candidate_items(self, user_id: str) -> list[Mapping[str, Any]]:
        items: list[Mapping[str, Any]] = []
        evaluated = 0
        cursor: Mapping[str, Any] | None = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_CANDIDATE_QUERY_PAGES):
            request: dict[str, object] = {
                "TableName": self._table_name,
                "KeyConditionExpression": "#pk=:pk AND begins_with(#sk,:candidate)",
                "FilterExpression": "#status=:visible",
                "ExpressionAttributeNames": {
                    "#pk": "PK",
                    "#sk": "SK",
                    "#status": "status",
                },
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"USER#{user_id}"},
                    ":candidate": {"S": "CANDIDATE#"},
                    ":visible": {"S": "VISIBLE"},
                },
                "ConsistentRead": True,
                "Limit": CANDIDATE_PAGE_SIZE,
            }
            if cursor is not None:
                request["ExclusiveStartKey"] = dict(cursor)
            response = self._client.query(**request)
            page = response.get("Items")
            if not isinstance(page, list) or any(
                not isinstance(item, Mapping) for item in page
            ):
                raise TypeError("Candidate query is invalid")
            items.extend(page)
            scanned_count = response.get("ScannedCount", len(page))
            if type(scanned_count) is not int or scanned_count < len(page):
                raise TypeError("Candidate query count is invalid")
            evaluated += scanned_count
            if (
                len(items) > MAX_VISIBLE_CANDIDATES
                or evaluated > MAX_EVALUATED_CANDIDATES
            ):
                raise RuntimeError("Candidate read exceeds the bounded mobile view")
            next_cursor = response.get("LastEvaluatedKey")
            if not next_cursor:
                return items
            if not isinstance(next_cursor, Mapping):
                raise TypeError("Candidate query cursor is invalid")
            cursor_key = json.dumps(next_cursor, sort_keys=True, separators=(",", ":"))
            if cursor_key in seen_cursors:
                raise RuntimeError("Candidate query cursor did not advance")
            seen_cursors.add(cursor_key)
            cursor = next_cursor
        raise RuntimeError("Candidate query exceeded its page bound")

    def _active_suppressions(self, user_id: str) -> dict[str, str]:
        response = self._client.query(
            TableName=self._table_name,
            KeyConditionExpression="#pk=:pk AND begins_with(#sk,:suppression)",
            FilterExpression="active=:active",
            ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
            ExpressionAttributeValues={
                ":pk": {"S": f"USER#{user_id}"},
                ":suppression": {"S": "SUPPRESSION#"},
                ":active": {"BOOL": True},
            },
            ConsistentRead=True,
            Limit=101,
        )
        items = response.get("Items")
        if not isinstance(items, list) or len(items) > 100:
            raise RuntimeError("Suggestion suppression read exceeds its bound")
        return {
            group_id: source_candidate_id
            for item in items
            if isinstance(item, Mapping)
            and (group_id := _string(item, "group_id")) is not None
            and (source_candidate_id := _string(item, "source_candidate_id"))
            is not None
        }


def default_suggestion_service() -> SuggestionService:
    return SuggestionService(DynamoSuggestionStore.from_environment())


def _candidate_from_item(
    value: object, *, expected_status: str = "VISIBLE"
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("Candidate item is invalid")
    candidate_id = _string(value, "candidate_id")
    outcome = _string(value, "outcome")
    summary = _string(value, "summary")
    group_id = _string(value, "primary_group_id")
    risk = _string(value, "risk")
    status = _string(value, "status")
    confidence = _number(value, "confidence")
    version = _integer(value, "version")
    created_at = _string(value, "created_at")
    updated_at = _string(value, "updated_at")
    if (
        None in {candidate_id, outcome, summary, group_id, created_at, updated_at}
        or risk not in RISK_ORDER
        or status != expected_status
        or confidence is None
        or not 0 <= confidence <= 1
        or version is None
        or version < 1
    ):
        raise TypeError("Candidate item fields are invalid")
    return {
        "candidate_id": candidate_id,
        "provider": _string(value, "provider") or "google",
        "source_type": _string(value, "source_type") or "CONNECTED_SIGNAL",
        "outcome": outcome,
        "summary": summary,
        "why_now": _string(value, "why_now") or "Review the suggested next step.",
        "opportunity_type": _string(value, "opportunity_type"),
        "evidence_refs": _string_list(value, "evidence_refs"),
        "confidence": confidence,
        "risk": risk,
        "primary_group_id": group_id,
        "tags": _string_list(value, "tags"),
        "proposed_actions": _proposed_actions(value),
        "status": status,
        "version": version,
        "created_at": created_at,
        "updated_at": updated_at,
        **(
            {
                "mail_profile_version": _integer(value, "mail_profile_version"),
                "mail_scan_id": _string(value, "mail_scan_id"),
            }
            if _integer(value, "mail_profile_version") is not None
            and _string(value, "mail_scan_id") is not None
            else {}
        ),
    }


def _proposed_actions(item: Mapping[str, Any]) -> list[dict[str, object]]:
    encoded = _string(item, "proposed_actions_json")
    if encoded is None:
        return []
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise TypeError("Candidate proposed actions are invalid") from error
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 3
        or any(not isinstance(action, dict) for action in value)
    ):
        raise TypeError("Candidate proposed actions are invalid")
    return value


def _string(item: Mapping[str, Any], name: str) -> str | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("S"), str):
        return value["S"]
    return None


def _string_list(item: Mapping[str, Any], name: str) -> list[str]:
    value = item.get(name)
    raw = value.get("L", []) if isinstance(value, Mapping) else []
    if not isinstance(raw, list):
        raise TypeError(f"Candidate {name} is invalid")
    values: list[str] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("S"), str):
            raise TypeError(f"Candidate {name} is invalid")
        values.append(entry["S"])
    return values


def _number(item: Mapping[str, Any], name: str) -> float | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("N"), str):
        return float(value["N"])
    return None


def _integer(item: Mapping[str, Any], name: str) -> int | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("N"), str):
        return int(value["N"])
    return None


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise SuggestionInputError(f"{name} is invalid")
    return value


def _version(value: object) -> int:
    if type(value) is not int or value < 1:
        raise SuggestionInputError("expected_version is invalid")
    return value


def _candidate_key(user_id: str, candidate_id: str) -> dict[str, dict[str, str]]:
    return {
        "PK": {"S": f"USER#{user_id}"},
        "SK": {"S": f"CANDIDATE#{candidate_id}"},
    }


def _error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    nested = response.get("Error")
    return str(nested.get("Code")) if isinstance(nested, Mapping) else None
