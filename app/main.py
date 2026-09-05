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
from app.risk_client import HttpRiskClient, RiskClient
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

DESCRIPTION = """
Payment lifecycle orchestration: reserve, capture and release settlement
against the ledger.

A payment moves money in two phases through three positions, so no stage can
leave funds in an impossible place. **Reserve** moves the customer's funds into
Payment Suspense before any provider is called, **capture** moves them on to
Settlement Clearing once the provider confirms, and **release** returns them to
the customer when it does not. Failure is a compensating transfer, never a
delete, because the ledger is append-only.

**Correctness and resilience properties**

- The provider is never called before funds are reserved
- Each reserve, capture and release has an at-most-once ledger effect, keyed
  deterministically and retried to completion
- An ambiguous timeout pins the payment to its provider and reconciles, never falling back
- Duplicate provider callbacks are deduplicated on `(provider, provider_reference)`
- Every state change and its outbox event commit in the same transaction

**Events**

State changes are published through a transactional outbox to Pub/Sub with the
ABS event envelope, at least once, deduplicated by consumers on `event_id`.
"""

TAGS = [
    {"name": "System", "description": "Health and service status."},
    {"name": "Payments", "description": "Create a payment and inspect its state and history."},
    {
        "name": "Reconciliation",
        "description": "Resolve a payment left UNKNOWN by an ambiguous provider timeout.",
    },
    {
        "name": "Provider Callbacks",
        "description": "Asynchronous provider outcomes, deduplicated on arrival.",
    },
    {
        "name": "Event Delivery",
        "description": "Transactional outbox relay. At-least-once delivery to Pub/Sub.",
    },
]

app = FastAPI(
    title="Payment Orchestrator",
    version="0.1.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
)

# A single router so circuit-breaker state persists across requests.
_router = ProviderRouter([NorthPay(), RapidPay(), LegacyPay()])

# A single ledger client so the authentication token and the HTTP connection
# pool are reused across requests. A fresh client per request would re-run the
# ledger's bcrypt login on every payment, which dominates payment latency.
_ledger_client = HttpLedgerClient(
    base_url=config.LEDGER_BASE_URL,
    username=config.LEDGER_USERNAME,
    password=config.LEDGER_PASSWORD,
    suspense_account_id=UUID(config.SUSPENSE_ACCOUNT_ID),
    settlement_account_id=UUID(config.SETTLEMENT_ACCOUNT_ID),
)


# A single risk client so its HTTP connection pool is reused across payments.
_risk_client = HttpRiskClient(base_url=config.RISK_BASE_URL)


def get_ledger_client() -> LedgerClient:
    return _ledger_client


def get_provider_router() -> ProviderRouter:
    return _router


def get_risk_client() -> RiskClient:
    return _risk_client


def provide_transport():
    return get_transport()


class ProviderCallback(BaseModel):
    provider: str
    provider_reference: str
    outcome: str


@app.get("/health", tags=["System"], summary="Liveness and database probe")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.post(
    "/payments",
    response_model=PaymentResponse,
    status_code=201,
    tags=["Payments"],
    summary="Create and process a payment",
)
def post_payment(
    data: PaymentCreate,
    db: Session = Depends(get_db),
    ledger: LedgerClient = Depends(get_ledger_client),
    router: ProviderRouter = Depends(get_provider_router),
    risk: RiskClient = Depends(get_risk_client),
):
    payment = create_payment(db, data)
    payment = process_payment(db, payment, router, ledger, risk)
    return payment


@app.get(
    "/payments/{payment_id}",
    response_model=PaymentResponse,
    tags=["Payments"],
    summary="Retrieve a payment",
)
def get_payment(payment_id: UUID, db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


@app.get(
    "/payments/{payment_id}/events",
    tags=["Payments"],
    summary="Payment state history",
)
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


@app.post(
    "/payments/{payment_id}/reconcile",
    response_model=PaymentResponse,
    tags=["Reconciliation"],
    summary="Reconcile an UNKNOWN payment",
)
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


@app.post(
    "/payments/{payment_id}/callback",
    tags=["Provider Callbacks"],
    summary="Handle a provider callback",
)
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


@app.get(
    "/outbox/pending",
    tags=["Event Delivery"],
    summary="List unpublished events",
)
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


@app.post(
    "/outbox/publish",
    tags=["Event Delivery"],
    summary="Relay pending events to the broker",
)
def outbox_publish(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    transport=Depends(provide_transport),
):
    return publish_pending(db, transport, limit)
