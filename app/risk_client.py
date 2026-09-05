"""Client for the risk engine's decision API.

The orchestrator asks the risk engine to decide a payment before it reserves any
funds. The call is idempotent on a deterministic evaluation_id, so a retry after
a crash returns the original decision rather than making a second one. If the
engine is unreachable or times out, the caller treats it as a hold for review,
never as an allow: uncertainty must not move money (ABS-REQ-013).
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import httpx

# A fixed namespace so a payment always maps to the same evaluation_id, which is
# what makes the risk call idempotent under retry.
_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def evaluation_id_for(payment_id: UUID) -> UUID:
    return uuid.uuid5(_NAMESPACE, f"risk:{payment_id}")


class RiskError(Exception):
    pass


class RiskUnavailable(RiskError):
    pass


@dataclass
class RiskResult:
    decision: str  # "allow" | "review" | "block"
    score: int
    reasons: list


class RiskClient(Protocol):
    def evaluate(
        self,
        evaluation_id: UUID,
        payment_id: UUID,
        account_id: UUID,
        amount: Decimal,
        destination: str,
        correlation_id: UUID,
    ) -> RiskResult: ...


class HttpRiskClient:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def evaluate(
        self,
        evaluation_id: UUID,
        payment_id: UUID,
        account_id: UUID,
        amount: Decimal,
        destination: str,
        correlation_id: UUID,
    ) -> RiskResult:
        body = {
            "evaluation_id": str(evaluation_id),
            "payment_id": str(payment_id),
            "account_id": str(account_id),
            "amount": str(amount),
            "destination": destination,
            "correlation_id": str(correlation_id),
        }
        try:
            resp = self._client.post(f"{self._base_url}/risk/evaluate", json=body)
        except httpx.HTTPError as e:
            raise RiskUnavailable(str(e)) from e

        if resp.status_code == 200:
            data = resp.json()
            return RiskResult(
                decision=data["decision"], score=data["score"], reasons=data["reasons"]
            )
        if resp.status_code >= 500:
            raise RiskUnavailable(f"risk engine {resp.status_code}")
        raise RiskError(f"unexpected {resp.status_code}: {resp.text}")
