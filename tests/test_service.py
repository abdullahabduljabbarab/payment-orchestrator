import json
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.models import OutboxEvent, ProviderAttempt
from app.providers import Outcome, ScriptedProvider
from app.router import ProviderRouter
from app.schemas import PaymentCreate
from app.service import (
    create_payment,
    handle_callback,
    process_payment,
    reconcile_payment,
)
from app.states import PaymentState
from tests.fakes import FakeLedgerClient


def _new_payment(db, amount: str):
    return create_payment(
        db,
        PaymentCreate(
            account_id=uuid4(), amount=Decimal(amount), destination="acme"
        ),
    )


def _router(*providers) -> ProviderRouter:
    return ProviderRouter(list(providers))


def _one_provider(outcome: Outcome, reconcile=Outcome.SUCCESS) -> ProviderRouter:
    return _router(ScriptedProvider("P1", [outcome], reconcile_outcome=reconcile))


def _events(db, payment_id) -> list[str]:
    rows = db.execute(
        select(OutboxEvent)
        .where(OutboxEvent.aggregate_id == payment_id)
        .order_by(OutboxEvent.created_at.asc())
    ).scalars().all()
    return [r.event_type for r in rows]


# ---- reserve stage ----

def test_create_payment_starts_received(db):
    payment = _new_payment(db, "100.00")
    assert payment.state == PaymentState.RECEIVED
    assert _events(db, payment.id) == ["payment.received"]


def test_insufficient_funds_fails_and_never_reaches_provider(db):
    ledger = FakeLedgerClient(insufficient=True)
    provider = ScriptedProvider("P1", [Outcome.SUCCESS])
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, _router(provider), ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.FAILED
    assert payment.reserve_tx_id is None
    assert provider.submit_calls == 0
    assert "payment.reservation_failed" in _events(db, payment.id)


def test_high_value_goes_to_review_without_reserving(db):
    ledger = FakeLedgerClient()
    payment = _new_payment(db, "5000.00")
    process_payment(db, payment, _one_provider(Outcome.SUCCESS), ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.RISK_REVIEW
    assert ledger.calls == []


def test_over_limit_is_rejected(db):
    ledger = FakeLedgerClient()
    payment = _new_payment(db, "10000.00")
    process_payment(db, payment, _one_provider(Outcome.SUCCESS), ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.REJECTED
    assert ledger.calls == []


def test_ledger_unavailable_leaves_payment_reserving(db):
    ledger = FakeLedgerClient(unavailable=True)
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, _one_provider(Outcome.SUCCESS), ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.RESERVING
    assert payment.reserve_tx_id is None


# ---- provider stage ----

def test_provider_success_settles(db):
    ledger = FakeLedgerClient()
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, _one_provider(Outcome.SUCCESS), ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.SETTLED
    assert payment.reserve_tx_id is not None
    assert payment.capture_tx_id is not None
    assert ledger.calls == ["reserve", "capture"]
    assert _events(db, payment.id) == [
        "payment.received",
        "payment.approved",
        "payment.reserved",
        "payment.provider_succeeded",
        "payment.captured",
        "payment.settled",
    ]


def test_provider_failure_releases_and_fails(db):
    ledger = FakeLedgerClient()
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, _one_provider(Outcome.FAILED), ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.FAILED
    assert payment.release_tx_id is not None
    assert payment.capture_tx_id is None
    assert ledger.calls == ["reserve", "release"]
    events = _events(db, payment.id)
    assert "payment.provider_failed" in events
    assert "payment.released" in events
    assert "payment.failed" in events


def test_timeout_pins_to_provider_and_does_not_fall_back(db):
    ledger = FakeLedgerClient()
    first = ScriptedProvider("P1", [Outcome.TIMEOUT])
    second = ScriptedProvider("P2", [Outcome.SUCCESS])
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, _router(first, second), ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.UNKNOWN
    assert payment.provider == "P1"
    assert first.submit_calls == 1
    assert second.submit_calls == 0  # ambiguous timeout never falls back
    assert "payment.unknown" in _events(db, payment.id)


def test_unavailable_falls_back_to_next_provider(db):
    ledger = FakeLedgerClient()
    first = ScriptedProvider("P1", [Outcome.UNAVAILABLE])
    second = ScriptedProvider("P2", [Outcome.SUCCESS])
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, _router(first, second), ledger)

    db.refresh(payment)
    assert payment.state == PaymentState.SETTLED
    assert payment.provider == "P2"
    assert first.submit_calls == 1
    assert second.submit_calls == 1


def test_all_providers_unavailable_releases_reservation(db):
    ledger = FakeLedgerClient()
    payment = _new_payment(db, "100.00")
    process_payment(
        db,
        payment,
        _router(
            ScriptedProvider("P1", [Outcome.UNAVAILABLE]),
            ScriptedProvider("P2", [Outcome.UNAVAILABLE]),
        ),
        ledger,
    )

    db.refresh(payment)
    assert payment.state == PaymentState.FAILED
    assert payment.release_tx_id is not None


# ---- reconciliation of UNKNOWN ----

def test_reconcile_unknown_success_settles(db):
    ledger = FakeLedgerClient()
    router = _one_provider(Outcome.TIMEOUT, reconcile=Outcome.SUCCESS)
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, router, ledger)
    assert payment.state == PaymentState.UNKNOWN

    reconcile_payment(db, payment, router.provider("P1"), ledger)
    db.refresh(payment)
    assert payment.state == PaymentState.SETTLED
    assert payment.capture_tx_id is not None


def test_reconcile_unknown_failure_fails(db):
    ledger = FakeLedgerClient()
    router = _one_provider(Outcome.TIMEOUT, reconcile=Outcome.FAILED)
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, router, ledger)

    reconcile_payment(db, payment, router.provider("P1"), ledger)
    db.refresh(payment)
    assert payment.state == PaymentState.FAILED
    assert payment.release_tx_id is not None


# ---- duplicate callback protection (ABS-REQ-003) ----

def test_duplicate_callback_captures_once(db):
    ledger = FakeLedgerClient()
    router = _one_provider(Outcome.TIMEOUT)
    payment = _new_payment(db, "100.00")
    process_payment(db, payment, router, ledger)
    assert payment.state == PaymentState.UNKNOWN

    first = handle_callback(db, payment, "P1", "ref-1", "success", ledger)
    second = handle_callback(db, payment, "P1", "ref-1", "success", ledger)
    third = handle_callback(db, payment, "P1", "ref-1", "success", ledger)

    db.refresh(payment)
    assert first == "processed"
    assert second == "duplicate"
    assert third == "duplicate"
    assert payment.state == PaymentState.SETTLED
    assert ledger.calls.count("capture") == 1

    attempts = db.execute(
        select(ProviderAttempt).where(ProviderAttempt.payment_id == payment.id)
    ).scalars().all()
    callback_rows = [a for a in attempts if a.callback_type == "callback"]
    assert len(callback_rows) == 1


def test_reservation_payload_carries_tx_id(db):
    ledger = FakeLedgerClient()
    payment = _new_payment(db, "250.00")
    process_payment(db, payment, _one_provider(Outcome.SUCCESS), ledger)

    row = db.execute(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == payment.id,
            OutboxEvent.event_type == "payment.reserved",
        )
    ).scalar_one()
    payload = json.loads(row.payload)
    assert payload["reserve_tx_id"] == str(payment.reserve_tx_id)
    assert payload["amount"] == "250.00"
