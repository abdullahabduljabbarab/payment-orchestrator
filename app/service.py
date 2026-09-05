"""Orchestration: driving a payment through the state machine.

Every state change goes through `_advance`, which enforces the transition, writes
the append-only payment event, writes the outbox event in the same database
transaction, and commits. A payment is therefore always left in a durable,
legal state, which is what makes crash recovery a matter of resuming from the
state on disk.
"""

import json
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.ledger_client import InsufficientFunds, LedgerClient, LedgerUnavailable
from app.models import OutboxEvent, Payment, PaymentEvent
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
    db.add(
        OutboxEvent(
            aggregate_type="payment",
            aggregate_id=payment.id,
            event_type=event_type,
            payload=json.dumps(payload),
            correlation_id=payment.correlation_id,
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


def process_payment(db: Session, payment: Payment, ledger: LedgerClient) -> Payment:
    """Drive a newly received payment through risk and reservation.

    The provider call, capture and release are added in the next slice. For now
    a payment ends this pass in FUNDS_RESERVED (ready for the provider),
    RISK_REVIEW (held), FAILED (insufficient funds) or REJECTED (risk block).
    """
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
    return payment
