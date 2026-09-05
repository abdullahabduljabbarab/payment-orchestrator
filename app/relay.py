"""The outbox relay: turning stored outbox rows into published ABS events.

Each row is wrapped in the full envelope from EVENT_CATALOGUE.md before it is
sent. Rows are published oldest first, and the relay stops at the first failure
so ordering is preserved and the failed row plus everything after it stays
pending for the next call. A row is marked published only once the transport
has accepted it.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OutboxEvent

PRODUCER = "payment-orchestrator"


def build_envelope(row: OutboxEvent) -> dict:
    return {
        "event_id": str(row.id),
        "event_type": row.event_type,
        "event_version": row.event_version,
        "occurred_at": row.created_at.isoformat() if row.created_at else None,
        "producer": PRODUCER,
        "correlation_id": str(row.correlation_id),
        "causation_id": str(row.causation_id),
        "aggregate_id": str(row.aggregate_id),
        "payload": json.loads(row.payload),
    }


def pending_events(db: Session, limit: int = 50) -> list[OutboxEvent]:
    return list(
        db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.created_at.asc())
            .limit(limit)
        ).scalars().all()
    )


def publish_pending(db: Session, transport, limit: int = 50) -> dict:
    rows = pending_events(db, limit)
    published = 0
    failed = 0
    for row in rows:
        try:
            transport.publish(build_envelope(row))
        except Exception:
            # Leave this row and everything after it pending, preserving order.
            failed = len(rows) - published
            break
        row.published_at = datetime.now(tz=timezone.utc)
        published += 1
    db.commit()
    return {"published": published, "failed": failed, "transport": transport.name}
