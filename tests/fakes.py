from decimal import Decimal
from uuid import UUID, uuid4

from app.ledger_client import InsufficientFunds, LedgerUnavailable
from app.risk_client import RiskResult, RiskUnavailable


class FakeTransport:
    """Records published envelopes. Can be set to fail to exercise the relay's
    leave-pending-on-failure behaviour."""

    name = "fake"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.published: list[dict] = []

    def publish(self, envelope: dict) -> str:
        if self.fail:
            raise RuntimeError("transport unavailable")
        self.published.append(envelope)
        return f"fake:{envelope['event_id']}"


class FakeLedgerClient:
    """In-memory stand-in for the ledger. Idempotent by operation: calling the
    same operation for the same payment returns the same transaction id, the
    way the real ledger does for a repeated idempotency key."""

    def __init__(self, insufficient: bool = False, unavailable: bool = False):
        self._insufficient = insufficient
        self._unavailable = unavailable
        self.calls: list[str] = []
        self._tx: dict[str, UUID] = {}

    def _op(self, name: str, payment_id: UUID) -> UUID:
        self.calls.append(name)
        key = f"{payment_id}:{name}"
        if key not in self._tx:
            self._tx[key] = uuid4()
        return self._tx[key]

    def reserve(self, payment_id: UUID, account_id: UUID, amount: Decimal) -> UUID:
        if self._unavailable:
            self.calls.append("reserve")
            raise LedgerUnavailable("fake ledger unavailable")
        if self._insufficient:
            self.calls.append("reserve")
            raise InsufficientFunds("insufficient")
        return self._op("reserve", payment_id)

    def capture(self, payment_id: UUID, amount: Decimal) -> UUID:
        return self._op("capture", payment_id)

    def release(self, payment_id: UUID, account_id: UUID, amount: Decimal) -> UUID:
        return self._op("release", payment_id)


class FakeRiskClient:
    """In-memory risk engine stand-in. Returns a fixed decision, or raises
    RiskUnavailable to exercise the orchestrator's fail-to-review path."""

    def __init__(self, decision: str = "allow", unavailable: bool = False):
        self.decision = decision
        self.unavailable = unavailable
        self.calls = 0

    def evaluate(
        self, evaluation_id, payment_id, account_id, amount, destination, correlation_id
    ) -> RiskResult:
        self.calls += 1
        if self.unavailable:
            raise RiskUnavailable("engine down")
        return RiskResult(decision=self.decision, score=0, reasons=[])
