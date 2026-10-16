"""Backwards-compatibility shim re-exporting models from app.model."""
from app.model import (
    AuditEvent,
    Customer,
    Department,
    Notification,
    RequestStatus,
    RoutingRule,
    SupportRequest,
)
from app.model.common import utc_now

__all__ = [
    "AuditEvent",
    "Customer",
    "Department",
    "Notification",
    "RequestStatus",
    "RoutingRule",
    "SupportRequest",
    "utc_now",
]
