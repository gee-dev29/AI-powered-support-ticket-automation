"""Initial schema migration

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-10-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. customers table
    op.create_table(
        "customers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. departments table
    op.create_table(
        "departments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False, unique=True),
        sa.Column("notification_email", sa.String(length=320), nullable=True),
        sa.Column("slack_channel_id", sa.String(length=80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    # 3. routing_rules table
    op.create_table(
        "routing_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("category", sa.String(length=40), nullable=False, unique=True),
        sa.Column("department_id", sa.String(length=36), sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    # 4. support_requests table
    op.create_table(
        "support_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("customer_id", sa.String(length=36), sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("department_id", sa.String(length=36), sa.ForeignKey("departments.id"), nullable=True, index=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False, unique=True),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("handled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('needs_review', 'assigned', 'in_progress', 'waiting_on_customer', 'resolved', 'closed')",
            name="ck_support_requests_status",
        ),
        sa.CheckConstraint("priority IN ('normal', 'urgent')", name="ck_support_requests_priority"),
        sa.CheckConstraint("handled = (status IN ('resolved', 'closed'))", name="ck_support_requests_handled_matches_status"),
    )

    # 5. notifications outbox table
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("support_request_id", sa.String(length=36), sa.ForeignKey("support_requests.id"), nullable=False, index=True),
        sa.Column("notification_type", sa.String(length=40), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("destination", sa.String(length=320), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'processing', 'sent', 'failed')", name="ck_notifications_status"),
        sa.CheckConstraint("channel IN ('email', 'slack')", name="ck_notifications_channel"),
    )

    # 6. audit_events table
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("support_request_id", sa.String(length=36), sa.ForeignKey("support_requests.id"), nullable=False, index=True),
        sa.Column("actor_type", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("notifications")
    op.drop_table("support_requests")
    op.drop_table("routing_rules")
    op.drop_table("departments")
    op.drop_table("customers")
