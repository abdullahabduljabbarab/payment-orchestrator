"""Provider selection with a circuit breaker.

The router offers the available providers in order. A provider that fails
definitively too often has its breaker opened and is skipped until it has had
time to recover. Fallback to the next provider is a routing decision made only
on definitive failures; an ambiguous timeout never triggers fallback, it pins
the payment to its provider for reconciliation.
"""

import time

from app.providers import Provider


class CircuitBreaker:
    def __init__(self, threshold: int = 3, reset_after_seconds: float = 30.0):
        self._threshold = threshold
        self._reset_after = reset_after_seconds
        self._failures = 0
        self._opened_at: float | None = None

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self._reset_after:
            # Half-open: allow one attempt through to test recovery.
            self._opened_at = None
            self._failures = 0
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()


class ProviderRouter:
    def __init__(self, providers: list[Provider], threshold: int = 3):
        if not providers:
            raise ValueError("router needs at least one provider")
        self._providers = providers
        self._breakers = {p.name: CircuitBreaker(threshold) for p in providers}

    def available(self) -> list[Provider]:
        return [p for p in self._providers if not self._breakers[p.name].is_open()]

    def record_success(self, name: str) -> None:
        self._breakers[name].record_success()

    def record_failure(self, name: str) -> None:
        self._breakers[name].record_failure()

    def is_open(self, name: str) -> bool:
        return self._breakers[name].is_open()

    def provider(self, name: str) -> Provider:
        for p in self._providers:
            if p.name == name:
                return p
        raise KeyError(name)
