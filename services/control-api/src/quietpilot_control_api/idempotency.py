"""In-memory contract for later DynamoDB idempotency persistence."""

from dataclasses import dataclass


@dataclass(frozen=True)
class IdempotentResult[T]:
    created: bool
    value: T


class IdempotencyRegistry[T]:
    def __init__(self) -> None:
        self._values: dict[str, T] = {}

    def record(self, key: str, value: T) -> IdempotentResult[T]:
        if key in self._values:
            return IdempotentResult(created=False, value=self._values[key])
        self._values[key] = value
        return IdempotentResult(created=True, value=value)
