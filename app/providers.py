"""External payment providers.

A provider is asked to move money and responds with one of four outcomes:

    SUCCESS       the provider moved the money
    FAILED        the provider definitively declined
    TIMEOUT       the outcome is unknown; the request may or may not have landed
    UNAVAILABLE   the provider was unreachable before it could accept the request

SUCCESS and FAILED are definitive. UNAVAILABLE is a definitive failure to route,
so the orchestrator may fall back to another provider. TIMEOUT is ambiguous, so
the orchestrator must never fall back on it; it reconciles with the same
provider instead.
"""

import enum
import random
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4


class Outcome(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


DEFINITIVE = frozenset({Outcome.SUCCESS, Outcome.FAILED, Outcome.UNAVAILABLE})


@dataclass
class ProviderResponse:
    outcome: Outcome
    provider_reference: str | None = None


class Provider(Protocol):
    name: str

    def submit(self, payment_id: UUID, amount: Decimal) -> ProviderResponse: ...

    def reconcile(self, payment_id: UUID) -> ProviderResponse: ...


def _ref(name: str) -> str:
    return f"{name}-{uuid4().hex[:12]}"


class ScriptedProvider:
    """A provider whose outcomes are fixed in advance. Used to drive the
    orchestrator through specific scenarios deterministically."""

    def __init__(
        self,
        name: str,
        submit_outcomes: list[Outcome] | None = None,
        reconcile_outcome: Outcome = Outcome.SUCCESS,
    ):
        self.name = name
        self._submits: deque[Outcome] = deque(submit_outcomes or [Outcome.SUCCESS])
        self._reconcile = reconcile_outcome
        self.submit_calls = 0
        self.reconcile_calls = 0

    def submit(self, payment_id: UUID, amount: Decimal) -> ProviderResponse:
        self.submit_calls += 1
        outcome = self._submits.popleft() if self._submits else Outcome.SUCCESS
        ref = None if outcome in (Outcome.TIMEOUT, Outcome.UNAVAILABLE) else _ref(self.name)
        return ProviderResponse(outcome, ref)

    def reconcile(self, payment_id: UUID) -> ProviderResponse:
        self.reconcile_calls += 1
        ref = None if self._reconcile is Outcome.TIMEOUT else _ref(self.name)
        return ProviderResponse(self._reconcile, ref)


class _RandomProvider:
    """Base for the named simulators. Weights control how often each outcome
    occurs, so each provider has a different failure personality."""

    name = "random"

    def __init__(self, weights: dict[Outcome, float], seed: int | None = None):
        self._weights = weights
        self._rng = random.Random(seed)

    def _draw(self) -> Outcome:
        outcomes = list(self._weights.keys())
        weights = list(self._weights.values())
        return self._rng.choices(outcomes, weights=weights, k=1)[0]

    def submit(self, payment_id: UUID, amount: Decimal) -> ProviderResponse:
        outcome = self._draw()
        ref = None if outcome in (Outcome.TIMEOUT, Outcome.UNAVAILABLE) else _ref(self.name)
        return ProviderResponse(outcome, ref)

    def reconcile(self, payment_id: UUID) -> ProviderResponse:
        # A provider that timed out has usually still made a decision by the
        # time we reconcile, so reconciliation rarely stays ambiguous.
        outcome = self._rng.choices(
            [Outcome.SUCCESS, Outcome.FAILED], weights=[0.8, 0.2], k=1
        )[0]
        return ProviderResponse(outcome, _ref(self.name))


class NorthPay(_RandomProvider):
    name = "NorthPay"

    def __init__(self, seed: int | None = None):
        super().__init__(
            {Outcome.SUCCESS: 0.8, Outcome.UNAVAILABLE: 0.15, Outcome.FAILED: 0.05},
            seed,
        )


class RapidPay(_RandomProvider):
    name = "RapidPay"

    def __init__(self, seed: int | None = None):
        super().__init__(
            {Outcome.SUCCESS: 0.95, Outcome.FAILED: 0.05},
            seed,
        )


class LegacyPay(_RandomProvider):
    name = "LegacyPay"

    def __init__(self, seed: int | None = None):
        super().__init__(
            {Outcome.SUCCESS: 0.7, Outcome.TIMEOUT: 0.2, Outcome.FAILED: 0.1},
            seed,
        )
