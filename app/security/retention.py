import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.model.audit_event import AuditEvent
from app.model.notification import Notification
from app.model.support_request import SupportRequest

logger = logging.getLogger("support_requests.retention")


def enforce_data_retention(
    db: Session,
    retention_days: int = 90,
    anonymize_older_than_days: int = 180,
) -> dict[str, int]:
    """Execute organization-approved retention and privacy policies.
    
    1. Purges notifications and audit events older than `retention_days`.
    2. Anonymizes PII (subject, message) on resolved/closed support requests older than `anonymize_older_than_days`.
    """
    now = datetime.now(timezone.utc)
    cutoff_retention = now - timedelta(days=retention_days)
    cutoff_anonymize = now - timedelta(days=anonymize_older_than_days)

    # 1. Delete old notifications
    deleted_notifications = db.execute(
        delete(Notification).where(
            Notification.created_at < cutoff_retention,
            Notification.status.in_(["sent", "failed"]),
        )
    ).rowcount

    # 2. Delete old audit events
    deleted_audits = db.execute(
        delete(AuditEvent).where(AuditEvent.created_at < cutoff_retention)
    ).rowcount

    # 3. Anonymize closed/resolved tickets older than cutoff_anonymize
    anonymized_requests = db.execute(
        update(SupportRequest)
        .where(
            SupportRequest.status.in_(["resolved", "closed"]),
            SupportRequest.updated_at < cutoff_anonymize,
            SupportRequest.message != "[RETENTION_ANONYMIZED]",
        )
        .values(
            subject="[RETENTION_ANONYMIZED]",
            message="[RETENTION_ANONYMIZED]",
            review_reason=None,
        )
    ).rowcount

    db.commit()
    logger.info(
        "retention_policy_enforced deleted_notifications=%d deleted_audits=%d anonymized_requests=%d",
        deleted_notifications,
        deleted_audits,
        anonymized_requests,
    )
    return {
        "deleted_notifications": deleted_notifications,
        "deleted_audits": deleted_audits,
        "anonymized_requests": anonymized_requests,
    }
