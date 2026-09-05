"""Initial schema: payments, events, provider attempts, outbox

Revision ID: 001
Revises:
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

PAYMENT_STATE = postgresql.ENUM(
    "received",
    "risk_pending",
    "risk_review",
    "rejected",
    "approved",
    "reserving",
    "funds_reserved",
    "provider_pending",
    "capturing",
    "releasing",
    "unknown",
    "settled",
    "failed",
    name="paymentstate",
)


def upgrade():
    bind = op.get_bind()
    PAYMENT_STATE.create(bind, checkfirst=True)
    state = postgresql.ENUM(name="paymentstate", create_type=False)

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("destination", sa.String(), nullable=False),
        sa.Column("state", state, nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("reserve_tx_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("capture_tx_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("release_tx_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "payment_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id"),
            nullable=False,
        ),
        sa.Column("from_state", state, nullable=True),
        sa.Column("to_state", state, nullable=False),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "provider_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_reference", sa.String(), nullable=True),
        sa.Column("callback_type", sa.String(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "provider", "provider_reference", name="uq_provider_reference"
        ),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_published", "outbox_events", ["published_at"])


def downgrade():
    op.drop_table("outbox_events")
    op.drop_table("provider_attempts")
    op.drop_table("payment_events")
    op.drop_table("payments")
    postgresql.ENUM(name="paymentstate").drop(op.get_bind(), checkfirst=True)
