import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship

from app.states import PaymentState


class Base(DeclarativeBase):
    pass


class Payment(Base):
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    destination = Column(String, nullable=False)
    state = Column(Enum(PaymentState), nullable=False, default=PaymentState.RECEIVED)
    provider = Column(String, nullable=True)

    # The three ledger transactions that make up a payment's financial effect.
    reserve_tx_id = Column(UUID(as_uuid=True), nullable=True)
    capture_tx_id = Column(UUID(as_uuid=True), nullable=True)
    release_tx_id = Column(UUID(as_uuid=True), nullable=True)

    correlation_id = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    events = relationship("PaymentEvent", back_populates="payment")


class PaymentEvent(Base):
    """Append-only log of every state transition, so a payment's history is
    fully reconstructable."""

    __tablename__ = "payment_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id = Column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=False
    )
    from_state = Column(Enum(PaymentState), nullable=True)
    to_state = Column(Enum(PaymentState), nullable=False)
    detail = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    payment = relationship("Payment", back_populates="events")


class ProviderAttempt(Base):
    """One row per provider interaction. The unique constraint on
    (provider, provider_reference) is what makes a duplicate callback harmless:
    the second insert is rejected and the callback is ignored."""

    __tablename__ = "provider_attempts"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_reference", name="uq_provider_reference"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id = Column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=False
    )
    provider = Column(String, nullable=False)
    provider_reference = Column(String, nullable=True)
    callback_type = Column(String, nullable=True)
    outcome = Column(String, nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)


class OutboxEvent(Base):
    """Transactional outbox. An event is written in the same transaction as the
    state change it describes, so it exists if and only if that change
    committed."""

    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_published", "published_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_type = Column(String(50), nullable=False)
    aggregate_id = Column(UUID(as_uuid=True), nullable=False)
    event_type = Column(String(100), nullable=False)
    event_version = Column(Integer, nullable=False, default=1)
    payload = Column(Text, nullable=False)
    correlation_id = Column(UUID(as_uuid=True), nullable=False)
    # The event or request that directly caused this one, so a causal chain can
    # be reconstructed: received -> approved -> reserved -> ...
    causation_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    published_at = Column(DateTime(timezone=True), nullable=True)
