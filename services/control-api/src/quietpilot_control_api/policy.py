"""Authoritative grant validation outside the language model."""

from dataclasses import dataclass
from enum import StrEnum


class Risk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class GrantMode(StrEnum):
    ONCE = "ONCE"
    CONDITIONAL = "CONDITIONAL"
    STANDING = "STANDING"


class PolicyDenied(ValueError):
    """Raised when a requested grant or approval is not valid."""


@dataclass(frozen=True)
class ApprovalContext:
    approved_plan_hash: str
    current_plan_hash: str
    approved_plan_version: int
    current_plan_version: int
    expected_case_version: int
    current_case_version: int


def validate_grant(risk: Risk, grant_mode: GrantMode) -> None:
    if risk is Risk.HIGH and grant_mode is not GrantMode.ONCE:
        raise PolicyDenied("High-risk actions require one-time approval")


def validate_approval(context: ApprovalContext) -> None:
    if context.approved_plan_hash != context.current_plan_hash:
        raise PolicyDenied("Plan hash changed")
    if context.approved_plan_version != context.current_plan_version:
        raise PolicyDenied("Plan version changed")
    if context.expected_case_version != context.current_case_version:
        raise PolicyDenied("Case version changed")
