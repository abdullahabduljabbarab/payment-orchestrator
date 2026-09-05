import json
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.models import OutboxEvent
from app.schemas import PaymentCreate
from app.service import create_payment, process_payment
from app.states import PaymentState
from tests.fakes import FakeLedgerClient


def _new_payment(db, amount: str):
    return create_payment(
        db,
        PaymentCreate(
            account_id=uuid4(), amount=Decimal(amount), destination="acme"
        ),
    )


def _events(db, payment_id) -> list[str]:
    rows = db.execute(
        select(OutboxEvent)
        .where(OutboxEvent.aggregate_id == payment_id)
        .order_by(OutboxEvent.created_at.asc())
    ).scalars().all()
    return [r.event_type for r in rows]


def test_create_payment_starts_received(db):
    payment = _new_payment(db, "100.00")
    assert payment.state == PaymentState.RECEIVED
    assert _events(db, payment.id) == ["payment.received"]


def test_low_value_payment_reserves(db):
    ledger = FakeLedgerClient()
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.FUNDS_RESERVED
    assert payment.reserve_tx_id is not None
    assert ledger.calls == ["reserve"]
    assert _events(db, payment.id) == [
        "payment.received",
        "payment.approved",
        "payment.reserved",
    ]


def test_insufficient_funds_fails_and_never_reaches_provider(db):
    ledger = FakeLedgerClient(insufficient=True)
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.FAILED
    assert payment.reserve_tx_id is None
    # reserve was attempted, but capture and release never were
    assert ledger.calls == ["reserve"]
    assert "payment.reservation_failed" in _events(db, payment.id)


def test_high_value_goes_to_review_without_reserving(db):
    ledger = FakeLedgerClient()
    payment = _new_payment(db, "5000.00")
    process_payment(db, payment, ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.RISK_REVIEW
    assert ledger.calls == []


def test_over_limit_is_rejected(db):
    ledger = FakeLedgerClient()
    payment = _new_payment(db, "10000.00")
    process_payment(db, payment, ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.REJECTED
    assert ledger.calls == []
    assert "payment.rejected" in _events(db, payment.id)


def test_ledger_unavailable_leaves_payment_reserving(db):
    ledger = FakeLedgerClient(unavailable=True)
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.RESERVING
    assert payment.reserve_tx_id is None


def test_reservation_payload_carries_tx_id(db):
    ledger = FakeLedgerClient()
    payment = _new_payment(db, "250.00")
    process_payment(db, payment, ledger)

    row = db.execute(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == payment.id,
            OutboxEvent.event_type == "payment.reserved",
        )
    ).scalar_one()
    payload = json.loads(row.payload)
    assert payload["reserve_tx_id"] == str(payment.reserve_tx_id)
    assert payload["amount"] == "250.00"
