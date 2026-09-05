"""Orchestration: driving a payment through the state machine.

Every state change goes through `_advance`, which enforces the transition, writes
the append-only payment event, writes the outbox event in the same database
transaction, and commits. A payment is therefore always left in a durable,
legal state, which is what makes crash recovery a matter of resuming from the
state on disk.
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ledger_client import InsufficientFunds, LedgerClient, LedgerUnavailable
from app.models import OutboxEvent, Payment, PaymentEvent, ProviderAttempt
from app.providers import Outcome, Provider
from app.router import ProviderRouter
from app.schemas import PaymentCreate
from app.states import PaymentState, assert_transition

logger = logging.getLogger("orchestrator.service")

# Risk thresholds. This is a deterministic placeholder for the risk engine,
# which will later make this decision and emit risk events of its own.
REVIEW_THRESHOLD = Decimal("5000")
BLOCK_THRESHOLD = Decimal("10000")


def evaluate_risk(payment: Payment) -> tuple[str, list[str]]:
    if payment.amount >= BLOCK_THRESHOLD:
        return "block", ["amount_over_limit"]
    if payment.amount >= REVIEW_THRESHOLD:
        return "review", ["high_value"]
    return "allow", []


def _emit(db: Session, payment: Payment, event_type: str, payload: dict) -> None:
    # Causation points at the previous event for this payment, giving a causal
    # chain; the first event is caused by the originating request.
    last = db.execute(
        select(OutboxEvent.id)
        .where(OutboxEvent.aggregate_id == payment.id)
        .order_by(OutboxEvent.created_at.desc())
        .limit(1)
    ).scalar()
    causation = last if last is not None else payment.correlation_id
    db.add(
        OutboxEvent(
            aggregate_type="payment",
            aggregate_id=payment.id,
            event_type=event_type,
            payload=json.dumps(payload),
            correlation_id=payment.correlation_id,
            causation_id=causation,
        )
    )


def _advance(
    db: Session,
    payment: Payment,
    target: PaymentState,
    event_type: str | None = None,
    payload: dict | None = None,
    detail: str | None = None,
) -> None:
    assert_transition(payment.state, target)
    from_state = payment.state
    payment.state = target
    db.add(
        PaymentEvent(
            payment_id=payment.id,
            from_state=from_state,
            to_state=target,
            detail=detail,
        )
    )
    if event_type is not None:
        _emit(db, payment, event_type, payload or {})
    db.commit()
    logger.info(
        f"payment {from_state.value} -> {target.value}",
        extra={"payment_id": str(payment.id), "state": target.value},
    )


def create_payment(db: Session, data: PaymentCreate) -> Payment:
    payment = Payment(
        account_id=data.account_id,
        amount=data.amount,
        destination=data.destination,
        state=PaymentState.RECEIVED,
    )
    db.add(payment)
    db.flush()
    db.add(
        PaymentEvent(
            payment_id=payment.id,
            from_state=None,
            to_state=PaymentState.RECEIVED,
        )
    )
    _emit(
        db,
        payment,
        "payment.received",
        {
            "payment_id": str(payment.id),
            "account_id": str(payment.account_id),
            "amount": str(payment.amount),
            "destination": payment.destination,
        },
    )
    db.commit()
    db.refresh(payment)
    return payment


def _reserve(db: Session, payment: Payment, ledger: LedgerClient) -> None:
    _advance(db, payment, PaymentState.RESERVING)
    try:
        tx_id = ledger.reserve(payment.id, payment.account_id, payment.amount)
    except InsufficientFunds:
        _advance(
            db,
            payment,
            PaymentState.FAILED,
            event_type="payment.reservation_failed",
            payload={"payment_id": str(payment.id), "reason": "insufficient_funds"},
            detail="insufficient_funds",
        )
        return
    except LedgerUnavailable:
        # Leave the payment in RESERVING. It is durable and the reserve will be
        # retried; the deterministic idempotency key makes the retry safe.
        logger.warning(
            "ledger unavailable during reserve, payment left in RESERVING",
            extra={"payment_id": str(payment.id)},
        )
        return

    payment.reserve_tx_id = tx_id
    _advance(
        db,
        payment,
        PaymentState.FUNDS_RESERVED,
        event_type="payment.reserved",
        payload={
            "payment_id": str(payment.id),
            "reserve_tx_id": str(tx_id),
            "amount": str(payment.amount),
        },
    )


def _record_attempt(
    db: Session,
    payment: Payment,
    provider: str,
    reference: str | None,
    callback_type: str,
    outcome: str,
) -> None:
    db.add(
        ProviderAttempt(
            payment_id=payment.id,
            provider=provider,
            provider_reference=reference,
            callback_type=callback_type,
            outcome=outcome,
            processed_at=datetime.now(tz=timezone.utc),
        )
    )
    db.commit()


def _capture(db: Session, payment: Payment, ledger: LedgerClient) -> None:
    _advance(db, payment, PaymentState.CAPTURING)
    try:
        tx_id = ledger.capture(payment.id, payment.amount)
    except LedgerUnavailable:
        logger.warning(
            "ledger unavailable during capture, payment left in CAPTURING",
            extra={"payment_id": str(payment.id)},
        )
        return
    payment.capture_tx_id = tx_id
    _advance(
        db,
        payment,
        PaymentState.SETTLED,
        event_type="payment.captured",
        payload={"payment_id": str(payment.id), "capture_tx_id": str(tx_id)},
    )
    _emit(db, payment, "payment.settled", {"payment_id": str(payment.id)})
    db.commit()


def _release(db: Session, payment: Payment, ledger: LedgerClient, reason: str) -> None:
    _advance(db, payment, PaymentState.RELEASING)
    try:
        tx_id = ledger.release(payment.id, payment.account_id, payment.amount)
    except LedgerUnavailable:
        logger.warning(
            "ledger unavailable during release, payment left in RELEASING",
            extra={"payment_id": str(payment.id)},
        )
        return
    payment.release_tx_id = tx_id
    _advance(
        db,
        payment,
        PaymentState.FAILED,
        event_type="payment.released",
        payload={"payment_id": str(payment.id), "release_tx_id": str(tx_id)},
    )
    _emit(db, payment, "payment.failed", {"payment_id": str(payment.id), "reason": reason})
    db.commit()


def submit_to_provider(
    db: Session, payment: Payment, router: ProviderRouter, ledger: LedgerClient
) -> Payment:
    """Send a reserved payment to a provider, falling back on definitive
    unavailability but never on an ambiguous timeout."""
    for provider in router.available():
        payment.provider = provider.name
        if payment.state == PaymentState.FUNDS_RESERVED:
            _advance(db, payment, PaymentState.PROVIDER_PENDING)
        else:
            db.commit()

        resp = provider.submit(payment.id, payment.amount)
        _record_attempt(
            db, payment, provider.name, resp.provider_reference, "submit", resp.outcome.value
        )

        if resp.outcome is Outcome.SUCCESS:
            router.record_success(provider.name)
            _emit(
                db,
                payment,
                "payment.provider_succeeded",
                {"payment_id": str(payment.id), "provider": provider.name},
            )
            db.commit()
            _capture(db, payment, ledger)
            return payment

        if resp.outcome is Outcome.FAILED:
            router.record_success(provider.name)
            _emit(
                db,
                payment,
                "payment.provider_failed",
                {"payment_id": str(payment.id), "provider": provider.name},
            )
            db.commit()
            _release(db, payment, ledger, "provider_failed")
            return payment

        if resp.outcome is Outcome.TIMEOUT:
            # Ambiguous: pin to this provider, never fall back (ABS-REQ-012).
            _advance(
                db,
                payment,
                PaymentState.UNKNOWN,
                event_type="payment.unknown",
                payload={"payment_id": str(payment.id), "provider": provider.name},
            )
            return payment

        # UNAVAILABLE: a definitive failure to route. Open the breaker and try
        # the next available provider.
        router.record_failure(provider.name)

    # Every available provider was unavailable. The reservation is released so
    # the customer's funds are not stranded.
    _release(db, payment, ledger, "no_provider_available")
    return payment


def handle_callback(
    db: Session,
    payment: Payment,
    provider: str,
    provider_reference: str,
    outcome: str,
    ledger: LedgerClient,
) -> str:
    """Process a provider callback, ignoring duplicates.

    Deduplication is on `(provider, provider_reference)` with a unique database
    constraint, so a provider that sends the same callback more than once
    produces a single financial effect (ABS-REQ-003). The pre-check handles the
    common case; the constraint handles the race.
    """
    existing = db.execute(
        select(ProviderAttempt).where(
            ProviderAttempt.provider == provider,
            ProviderAttempt.provider_reference == provider_reference,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return "duplicate"

    db.add(
        ProviderAttempt(
            payment_id=payment.id,
            provider=provider,
            provider_reference=provider_reference,
            callback_type="callback",
            outcome=outcome,
            processed_at=datetime.now(tz=timezone.utc),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return "duplicate"

    if payment.state not in (PaymentState.PROVIDER_PENDING, PaymentState.UNKNOWN):
        # The payment was already resolved by another path; the callback is
        # recorded but changes nothing.
        return "ignored"

    if outcome == Outcome.SUCCESS.value:
        _emit(
            db,
            payment,
            "payment.provider_succeeded",
            {"payment_id": str(payment.id), "provider": provider},
        )
        db.commit()
        _capture(db, payment, ledger)
    elif outcome == Outcome.FAILED.value:
        _emit(
            db,
            payment,
            "payment.provider_failed",
            {"payment_id": str(payment.id), "provider": provider},
        )
        db.commit()
        _release(db, payment, ledger, "provider_failed")
    return "processed"


def reconcile_payment(
    db: Session, payment: Payment, provider: Provider, ledger: LedgerClient
) -> Payment:
    """Resolve an UNKNOWN payment by asking the provider what happened."""
    if payment.state is not PaymentState.UNKNOWN:
        return payment

    resp = provider.reconcile(payment.id)
    _record_attempt(
        db, payment, provider.name, resp.provider_reference, "reconcile", resp.outcome.value
    )

    if resp.outcome is Outcome.SUCCESS:
        _emit(
            db,
            payment,
            "payment.provider_succeeded",
            {"payment_id": str(payment.id), "provider": provider.name},
        )
        db.commit()
        _capture(db, payment, ledger)
    elif resp.outcome is Outcome.FAILED:
        _emit(
            db,
            payment,
            "payment.provider_failed",
            {"payment_id": str(payment.id), "provider": provider.name},
        )
        db.commit()
        _release(db, payment, ledger, "reconciled_failed")
    # Still ambiguous: leave the payment UNKNOWN for a later reconciliation.
    return payment


def process_payment(
    db: Session, payment: Payment, router: ProviderRouter, ledger: LedgerClient
) -> Payment:
    """Drive a newly received payment through risk, reservation and the
    provider. A payment ends this pass in SETTLED, FAILED, UNKNOWN (awaiting
    reconciliation), RISK_REVIEW (held) or REJECTED."""
    _advance(db, payment, PaymentState.RISK_PENDING)

    decision, reasons = evaluate_risk(payment)
    if decision == "block":
        _advance(
            db,
            payment,
            PaymentState.REJECTED,
            event_type="payment.rejected",
            payload={"payment_id": str(payment.id), "reasons": reasons},
            detail="risk_block",
        )
        return payment
    if decision == "review":
        _advance(db, payment, PaymentState.RISK_REVIEW, detail="risk_review")
        return payment

    _advance(
        db,
        payment,
        PaymentState.APPROVED,
        event_type="payment.approved",
        payload={"payment_id": str(payment.id)},
    )
    _reserve(db, payment, ledger)

    if payment.state is PaymentState.FUNDS_RESERVED:
        submit_to_provider(db, payment, router, ledger)
    return payment
