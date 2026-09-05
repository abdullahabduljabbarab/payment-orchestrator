from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.models import OutboxEvent
from app.providers import Outcome, ScriptedProvider
from app.relay import build_envelope, pending_events, publish_pending
from app.router import ProviderRouter
from app.schemas import PaymentCreate
from app.service import create_payment, handle_callback, process_payment
from app.states import PaymentState
from tests.fakes import FakeLedgerClient, FakeTransport

ENVELOPE_FIELDS = {
    "event_id",
    "event_type",
    "event_version",
    "occurred_at",
    "producer",
    "correlation_id",
    "causation_id",
    "aggregate_id",
    "payload",
}


def _settled_payment(db):
    ledger = FakeLedgerClient()
    router = ProviderRouter([ScriptedProvider("P1", [Outcome.SUCCESS])])
    payment = create_payment(
        db,
        PaymentCreate(account_id=uuid4(), amount=Decimal("100.00"), destination="acme"),
    )
    process_payment(db, payment, router, ledger)
    return payment


def test_envelope_has_full_abs_contract(db):
    payment = _settled_payment(db)
    row = pending_events(db)[0]
    envelope = build_envelope(row)

    assert set(envelope.keys()) == ENVELOPE_FIELDS
    assert envelope["producer"] == "payment-orchestrator"
    assert envelope["event_version"] == 1
    assert envelope["event_type"] == "payment.received"
    assert envelope["aggregate_id"] == str(payment.id)
    assert envelope["occurred_at"] is not None
    assert isinstance(envelope["payload"], dict)


def test_correlation_propagates_and_causation_chains(db):
    payment = _settled_payment(db)
    rows = pending_events(db, limit=200)

    # every event of a payment shares its correlation id
    assert all(r.correlation_id == payment.correlation_id for r in rows)

    # the first event is caused by the originating request; each later event is
    # caused by the one before it
    assert rows[0].causation_id == payment.correlation_id
    for prev, nxt in zip(rows, rows[1:]):
        assert nxt.causation_id == prev.id


def test_publish_marks_rows_published(db):
    _settled_payment(db)
    transport = FakeTransport()
    before = len(pending_events(db, limit=200))
    assert before > 0

    result = publish_pending(db, transport, limit=200)
    assert result["published"] == before
    assert result["failed"] == 0
    assert result["transport"] == "fake"
    assert pending_events(db) == []
    assert len(transport.published) == before


def test_failed_publish_leaves_everything_pending(db):
    _settled_payment(db)
    transport = FakeTransport(fail=True)
    before = len(pending_events(db, limit=200))

    result = publish_pending(db, transport, limit=200)
    assert result["published"] == 0
    assert result["failed"] == before
    assert len(pending_events(db, limit=200)) == before


def test_retry_after_failure_publishes(db):
    _settled_payment(db)
    assert publish_pending(db, FakeTransport(fail=True), limit=200)["published"] == 0

    result = publish_pending(db, FakeTransport(), limit=200)
    assert result["published"] > 0
    assert pending_events(db) == []


def test_event_id_is_stable_for_consumer_dedup(db):
    _settled_payment(db)
    row = pending_events(db)[0]
    # the event_id a consumer deduplicates on is the durable outbox row id
    assert build_envelope(row)["event_id"] == str(row.id)


def test_idempotent_retry_creates_no_duplicate_events(db):
    ledger = FakeLedgerClient()
    router = ProviderRouter([ScriptedProvider("P1", [Outcome.TIMEOUT])])
    payment = create_payment(
        db,
        PaymentCreate(account_id=uuid4(), amount=Decimal("100.00"), destination="acme"),
    )
    process_payment(db, payment, router, ledger)
    assert payment.state == PaymentState.UNKNOWN

    # the same callback three times must not create three captured/settled events
    handle_callback(db, payment, "P1", "ref-1", "success", ledger)
    handle_callback(db, payment, "P1", "ref-1", "success", ledger)
    handle_callback(db, payment, "P1", "ref-1", "success", ledger)

    types = db.execute(
        select(OutboxEvent.event_type).where(OutboxEvent.aggregate_id == payment.id)
    ).scalars().all()
    assert types.count("payment.captured") == 1
    assert types.count("payment.settled") == 1
