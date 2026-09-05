import logging
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.ledger_client import HttpLedgerClient, LedgerClient
from app.logging import setup_logging
from app.models import Payment, PaymentEvent
from app.providers import LegacyPay, NorthPay, RapidPay
from app.publisher import get_transport
from app.relay import pending_events, publish_pending
from app.router import ProviderRouter
from app.schemas import PaymentCreate, PaymentResponse
from app.service import (
    create_payment,
    handle_callback,
    process_payment,
    reconcile_payment,
)
from app.states import PaymentState

setup_logging()
logger = logging.getLogger("orchestrator.api")

app = FastAPI(
    title="Payment Orchestrator",
    version="0.1.0",
    description=(
        "Payment lifecycle orchestration with reserve, capture and release "
        "settlement against the ledger."
    ),
)

# A single router so circuit-breaker state persists across requests.
_router = ProviderRouter([NorthPay(), RapidPay(), LegacyPay()])


def get_ledger_client() -> LedgerClient:
    return HttpLedgerClient(
        base_url=config.LEDGER_BASE_URL,
        username=config.LEDGER_USERNAME,
        password=config.LEDGER_PASSWORD,
        suspense_account_id=UUID(config.SUSPENSE_ACCOUNT_ID),
        settlement_account_id=UUID(config.SETTLEMENT_ACCOUNT_ID),
    )


def get_provider_router() -> ProviderRouter:
    return _router


def provide_transport():
    return get_transport()


class ProviderCallback(BaseModel):
    provider: str
    provider_reference: str
    outcome: str


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.post("/payments", response_model=PaymentResponse, status_code=201)
def post_payment(
    data: PaymentCreate,
    db: Session = Depends(get_db),
    ledger: LedgerClient = Depends(get_ledger_client),
    router: ProviderRouter = Depends(get_provider_router),
):
    payment = create_payment(db, data)
    payment = process_payment(db, payment, router, ledger)
    return payment


@app.get("/payments/{payment_id}", response_model=PaymentResponse)
def get_payment(payment_id: UUID, db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


@app.get("/payments/{payment_id}/events")
def get_payment_events(payment_id: UUID, db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    rows = db.execute(
        select(PaymentEvent)
        .where(PaymentEvent.payment_id == payment_id)
        .order_by(PaymentEvent.created_at.asc())
    ).scalars().all()
    return [
        {
            "from_state": e.from_state.value if e.from_state else None,
            "to_state": e.to_state.value,
            "detail": e.detail,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows
    ]


@app.post("/payments/{payment_id}/reconcile", response_model=PaymentResponse)
def reconcile(
    payment_id: UUID,
    db: Session = Depends(get_db),
    ledger: LedgerClient = Depends(get_ledger_client),
    router: ProviderRouter = Depends(get_provider_router),
):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.state is not PaymentState.UNKNOWN:
        raise HTTPException(
            status_code=409, detail="Payment is not awaiting reconciliation"
        )
    provider = router.provider(payment.provider)
    reconcile_payment(db, payment, provider, ledger)
    db.refresh(payment)
    return payment


@app.post("/payments/{payment_id}/callback")
def callback(
    payment_id: UUID,
    body: ProviderCallback,
    db: Session = Depends(get_db),
    ledger: LedgerClient = Depends(get_ledger_client),
):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    status = handle_callback(
        db, payment, body.provider, body.provider_reference, body.outcome, ledger
    )
    return {"status": status}


@app.get("/outbox/pending")
def outbox_pending(
    limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)
):
    rows = pending_events(db, limit)
    return {
        "pending_count": len(rows),
        "events": [
            {
                "event_id": str(r.id),
                "event_type": r.event_type,
                "aggregate_id": str(r.aggregate_id),
                "correlation_id": str(r.correlation_id),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@app.post("/outbox/publish")
def outbox_publish(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    transport=Depends(provide_transport),
):
    return publish_pending(db, transport, limit)
