"""Canonical, immutable plan hashing."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any


class InvalidPlanRevision(ValueError):
    """Raised when an immutable plan revision breaks version/hash rules."""


def canonical_plan_json(plan: Mapping[str, Any]) -> str:
    return json.dumps(
        plan,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def plan_hash(plan: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_plan_json(plan).encode("utf-8")).hexdigest()


def validate_plan_revision(
    *,
    current_version: int,
    current_hash: str,
    next_version: int,
    next_hash: str,
    material_change: bool,
) -> None:
    if material_change:
        if next_version != current_version + 1:
            raise InvalidPlanRevision("Material plan changes require the next version")
        if next_hash == current_hash:
            raise InvalidPlanRevision("Material plan changes require a new hash")
        return
    if next_version != current_version or next_hash != current_hash:
        raise InvalidPlanRevision(
            "Non-material changes cannot revise the immutable plan"
        )
