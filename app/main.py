import logging
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.ledger_client import HttpLedgerClient, LedgerClient
from app.logging import setup_logging
from app.models import Payment, PaymentEvent
from app.schemas import PaymentCreate, PaymentResponse
from app.service import create_payment, process_payment

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


def get_ledger_client() -> LedgerClient:
    return HttpLedgerClient(
        base_url=config.LEDGER_BASE_URL,
        username=config.LEDGER_USERNAME,
        password=config.LEDGER_PASSWORD,
        suspense_account_id=UUID(config.SUSPENSE_ACCOUNT_ID),
        settlement_account_id=UUID(config.SETTLEMENT_ACCOUNT_ID),
    )


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.post("/payments", response_model=PaymentResponse, status_code=201)
def post_payment(
    data: PaymentCreate,
    db: Session = Depends(get_db),
    ledger: LedgerClient = Depends(get_ledger_client),
):
    payment = create_payment(db, data)
    payment = process_payment(db, payment, ledger)
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
