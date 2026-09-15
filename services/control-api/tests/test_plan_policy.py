import pytest
from quietpilot_control_api.idempotency import IdempotencyRegistry
from quietpilot_control_api.plan_hash import (
    InvalidPlanRevision,
    plan_hash,
    validate_plan_revision,
)
from quietpilot_control_api.policy import (
    ApprovalContext,
    GrantMode,
    PolicyDenied,
    Risk,
    validate_approval,
    validate_grant,
)


def test_plan_hash_is_key_order_independent() -> None:
    first = {"version": 1, "actions": [{"verb": "create", "target": "calendar"}]}
    second = {"actions": [{"target": "calendar", "verb": "create"}], "version": 1}
    assert plan_hash(first) == plan_hash(second)


def test_material_plan_change_changes_hash() -> None:
    first = {"version": 1, "actions": [{"target": "calendar", "title": "A"}]}
    changed = {"version": 2, "actions": [{"target": "calendar", "title": "B"}]}
    assert plan_hash(first) != plan_hash(changed)


def test_material_plan_revision_requires_next_version_and_hash() -> None:
    validate_plan_revision(
        current_version=2,
        current_hash="a" * 64,
        next_version=3,
        next_hash="b" * 64,
        material_change=True,
    )
    with pytest.raises(InvalidPlanRevision, match="new hash"):
        validate_plan_revision(
            current_version=2,
            current_hash="a" * 64,
            next_version=3,
            next_hash="a" * 64,
            material_change=True,
        )


@pytest.mark.parametrize("grant_mode", [GrantMode.CONDITIONAL, GrantMode.STANDING])
def test_high_risk_is_one_time_only(grant_mode: GrantMode) -> None:
    with pytest.raises(PolicyDenied, match="one-time"):
        validate_grant(Risk.HIGH, grant_mode)


def test_stale_plan_hash_is_rejected() -> None:
    with pytest.raises(PolicyDenied, match="hash"):
        validate_approval(
            ApprovalContext(
                approved_plan_hash="a" * 64,
                current_plan_hash="b" * 64,
                approved_plan_version=1,
                current_plan_version=1,
                expected_case_version=3,
                current_case_version=3,
            )
        )


@pytest.mark.parametrize(
    (
        "approved_plan_version",
        "current_plan_version",
        "expected_case_version",
        "current_case_version",
        "message",
    ),
    [
        (1, 2, 3, 3, "Plan version"),
        (1, 1, 2, 3, "Case version"),
    ],
)
def test_stale_versions_are_rejected(
    approved_plan_version: int,
    current_plan_version: int,
    expected_case_version: int,
    current_case_version: int,
    message: str,
) -> None:
    with pytest.raises(PolicyDenied, match=message):
        validate_approval(
            ApprovalContext(
                approved_plan_hash="a" * 64,
                current_plan_hash="a" * 64,
                approved_plan_version=approved_plan_version,
                current_plan_version=current_plan_version,
                expected_case_version=expected_case_version,
                current_case_version=current_case_version,
            )
        )


def test_duplicate_key_returns_original_result() -> None:
    registry: IdempotencyRegistry[str] = IdempotencyRegistry()
    first = registry.record("gmail:user:42", "candidate-1")
    duplicate = registry.record("gmail:user:42", "candidate-2")
    assert first.created is True
    assert duplicate.created is False
    assert duplicate.value == "candidate-1"
