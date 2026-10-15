import enum


class RequestStatus(str, enum.Enum):
    NEEDS_REVIEW = "needs_review"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    WAITING_ON_CUSTOMER = "waiting_on_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"
