"""Add event_version and causation_id to the outbox for the ABS envelope

Revision ID: 002
Revises: 001
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "outbox_events",
        sa.Column("event_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "outbox_events",
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=False),
    )


def downgrade():
    op.drop_column("outbox_events", "causation_id")
    op.drop_column("outbox_events", "event_version")
