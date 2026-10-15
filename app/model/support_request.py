import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.model.common import utc_now

if TYPE_CHECKING:
    from app.model.audit_event import AuditEvent
    from app.model.customer import Customer
    from app.model.department import Department
    from app.model.notification import Notification


class SupportRequest(Base):
    __tablename__ = "support_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('needs_review', 'assigned', 'in_progress', "
            "'waiting_on_customer', 'resolved', 'closed')",
            name="ck_support_requests_status",
        ),
        CheckConstraint("priority IN ('normal', 'urgent')", name="ck_support_requests_priority"),
        CheckConstraint(
            "handled = (status IN ('resolved', 'closed'))",
            name="ck_support_requests_handled_matches_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    handled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped["Customer"] = relationship(back_populates="requests")
    department: Mapped["Department | None"] = relationship()
    notifications: Mapped[list["Notification"]] = relationship(back_populates="support_request")
    events: Mapped[list["AuditEvent"]] = relationship(back_populates="support_request")
