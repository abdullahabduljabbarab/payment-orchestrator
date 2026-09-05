from decimal import Decimal
from uuid import UUID, uuid4

from app.ledger_client import InsufficientFunds, LedgerUnavailable


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
