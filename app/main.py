import hmac
import logging
import os
import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
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
from app.policy import classify_request
from app.schemas import (
    DepartmentAssignment,
    HealthResponse,
    StatusUpdate,
    SupportRequestCreate,
    SupportRequestResult,
    valid_idempotency_key,
)


app = FastAPI(
    title="Customer Support Request API",
    version="0.1.0",
    description="Validated support intake with deterministic routing and a durable notification outbox.",
)
logger = logging.getLogger("support_requests")

DbSession = Annotated[Session, Depends(get_db)]

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    RequestStatus.NEEDS_REVIEW.value: {
        RequestStatus.ASSIGNED.value,
        RequestStatus.IN_PROGRESS.value,
    },
    RequestStatus.ASSIGNED.value: {
        RequestStatus.IN_PROGRESS.value,
        RequestStatus.WAITING_ON_CUSTOMER.value,
        RequestStatus.RESOLVED.value,
    },
    RequestStatus.IN_PROGRESS.value: {
        RequestStatus.WAITING_ON_CUSTOMER.value,
        RequestStatus.RESOLVED.value,
    },
    RequestStatus.WAITING_ON_CUSTOMER.value: {
        RequestStatus.IN_PROGRESS.value,
        RequestStatus.RESOLVED.value,
    },
    RequestStatus.RESOLVED.value: {
        RequestStatus.IN_PROGRESS.value,
        RequestStatus.CLOSED.value,
    },
    RequestStatus.CLOSED.value: set(),
}


def _notification(
    request_id: str,
    notification_type: str,
    channel: str,
    destination: str,
    subject: str,
    message: str,
) -> Notification:
    return Notification(
        support_request_id=request_id,
        notification_type=notification_type,
        channel=channel,
        destination=destination,
        payload={"subject": subject, "message": message},
    )


def _add_customer_ack(db: Session, ticket_id: str, email: str) -> None:
    db.add(
        _notification(
            ticket_id,
            "customer_acknowledgement",
            "email",
            email,
            f"Support request received: {ticket_id}",
            f"We received your support request. Your reference number is {ticket_id}.",
        )
    )


def _result(
    ticket: SupportRequest, department: Department | None, duplicate: bool = False
) -> SupportRequestResult:
    return SupportRequestResult(
        id=uuid.UUID(ticket.id),
        category=ticket.category,
        priority=ticket.priority,
        status=RequestStatus(ticket.status),
        handled=ticket.handled,
        department=department.name if department else None,
        review_reason=ticket.review_reason,
        duplicate=duplicate,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post(
    "/requests",
    response_model=SupportRequestResult,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Existing request returned for duplicate delivery."}},
)
def create_request(
    body: SupportRequestCreate,
    response: Response,
    db: DbSession,
    idempotency_header: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=200)
    ],
) -> SupportRequestResult:
    try:
        idempotency_key = valid_idempotency_key(idempotency_header)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    existing = db.scalar(
        select(SupportRequest).where(SupportRequest.idempotency_key == idempotency_key)
    )
    if existing:
        response.status_code = status.HTTP_200_OK
        logger.info("duplicate_request id=%s", existing.id)
        return _result(existing, existing.department, duplicate=True)

    decision = classify_request(body.subject, body.message)
    category = decision.category
    routing = db.scalar(
        select(RoutingRule)
        .join(RoutingRule.department)
        .where(
            RoutingRule.category == category,
            RoutingRule.is_active.is_(True),
            Department.is_active.is_(True),
        )
    )
    if (
        category != "general"
        and (
            routing is None
            or not routing.department.notification_email
            and not routing.department.slack_channel_id
        )
    ):
        routing = db.scalar(
            select(RoutingRule)
            .join(RoutingRule.department)
            .where(
                RoutingRule.category == "general",
                RoutingRule.is_active.is_(True),
                Department.is_active.is_(True),
            )
        )
    route_is_fallback = routing is not None and routing.category != category
    department = routing.department if routing else None
    destinations: list[tuple[str, str]] = []
    if department and department.notification_email:
        destinations.append(("email", department.notification_email))
    if department and department.slack_channel_id:
        destinations.append(("slack", department.slack_channel_id))

    review_reason = decision.review_reason
    if route_is_fallback:
        review_reason = "no_active_department_route"
    if routing and not destinations:
        review_reason = "department_has_no_notification_destination"
    if not routing and not review_reason:
        review_reason = "no_active_department_route"

    requires_review = decision.needs_human_review or review_reason is not None
    ticket = SupportRequest(
        department_id=department.id if department else None,
        idempotency_key=idempotency_key,
        subject=body.subject,
        message=body.message,
        category=category,
        priority=decision.priority,
        status=(
            RequestStatus.NEEDS_REVIEW.value
            if requires_review
            else RequestStatus.ASSIGNED.value
        ),
        handled=False,
        review_reason=review_reason,
    )

    try:
        customer = db.scalar(select(Customer).where(Customer.email == body.contact_email))
        if customer is None:
            customer = Customer(name=body.contact_name, email=body.contact_email)
            db.add(customer)
            db.flush()
        ticket.customer = customer
        db.add(ticket)
        db.flush()

        db.add(
            AuditEvent(
                support_request_id=ticket.id,
                actor_type="system",
                event_type="request_received",
                details={"status": ticket.status, "category": category},
            )
        )
        _add_customer_ack(db, ticket.id, body.contact_email)

        if requires_review and department and destinations:
            review_message = (
                f"Support request {ticket.id} needs human review. "
                f"Reason: {review_reason}. Category: {category}. "
                f"Priority: {decision.priority}.\n\n"
                f"Subject: {ticket.subject}\nMessage: {ticket.message}"
            )
            for channel, destination in destinations:
                db.add(
                    _notification(
                        ticket.id,
                        "human_review",
                        channel,
                        destination,
                        f"Support request needs review: {ticket.id}",
                        review_message,
                    )
                )
        elif not requires_review and department:
            team_message = (
                f"New support request {ticket.id}. "
                f"Category: {category}. Priority: {decision.priority}. "
                f"\n\nSubject: {ticket.subject}\nMessage: {ticket.message}"
            )
            for channel, destination in destinations:
                db.add(
                    _notification(
                        ticket.id,
                        "department_assignment",
                        channel,
                        destination,
                        f"New support request {ticket.id}",
                        team_message,
                    )
                )
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.scalar(
            select(SupportRequest).where(
                SupportRequest.idempotency_key == idempotency_key
            )
        )
        if duplicate is None:
            raise
        response.status_code = status.HTTP_200_OK
        logger.info("duplicate_request id=%s", duplicate.id)
        return _result(duplicate, duplicate.department, duplicate=True)

    logger.info(
        "request_created id=%s category=%s priority=%s status=%s",
        ticket.id,
        ticket.category,
        ticket.priority,
        ticket.status,
    )
    return _result(ticket, department)


def _require_support_key(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    configured_key = os.getenv("SUPPORT_ADMIN_API_KEY")
    if not configured_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Support status updates are disabled until SUPPORT_ADMIN_API_KEY is configured.",
        )
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not hmac.compare_digest(supplied, configured_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid support key.")


@app.patch(
    "/requests/{request_id}/status",
    response_model=SupportRequestResult,
    dependencies=[Depends(_require_support_key)],
)
def update_request_status(
    request_id: uuid.UUID,
    body: StatusUpdate,
    db: DbSession,
) -> SupportRequestResult:
    ticket = db.get(SupportRequest, str(request_id))
    if ticket is None:
        raise HTTPException(status_code=404, detail="Support request not found.")

    new_status = body.status.value
    if new_status == ticket.status:
        return _result(ticket, ticket.department)

    if new_status not in ALLOWED_TRANSITIONS[ticket.status]:
        raise HTTPException(
            status_code=409,
            detail=f"Transition from {ticket.status} to {new_status} is not allowed.",
        )

    old_status = ticket.status
    ticket.status = new_status
    ticket.handled = new_status in {
        RequestStatus.RESOLVED.value,
        RequestStatus.CLOSED.value,
    }
    ticket.resolved_at = (
        utc_now()
        if ticket.handled
        else None
    )
    ticket.review_reason = None if new_status != RequestStatus.NEEDS_REVIEW.value else ticket.review_reason
    db.add(
        AuditEvent(
            support_request_id=ticket.id,
            actor_type="support_agent",
            event_type="status_changed",
            details={"from": old_status, "to": new_status},
        )
    )
    customer_email = ticket.customer.email
    db.add(
        _notification(
            ticket.id,
            f"customer_status_{new_status}",
            "email",
            customer_email,
            f"Support request update: {ticket.id}",
            f"Your support request {ticket.id} is now {new_status.replace('_', ' ')}.",
        )
    )
    db.commit()
    db.refresh(ticket)
    logger.info("request_status_changed id=%s from=%s to=%s", ticket.id, old_status, new_status)
    return _result(ticket, ticket.department)


@app.post(
    "/requests/{request_id}/assignment",
    response_model=SupportRequestResult,
    dependencies=[Depends(_require_support_key)],
)
def assign_request(
    request_id: uuid.UUID,
    body: DepartmentAssignment,
    db: DbSession,
) -> SupportRequestResult:
    ticket = db.get(SupportRequest, str(request_id))
    if ticket is None:
        raise HTTPException(status_code=404, detail="Support request not found.")
    if ticket.status in {RequestStatus.RESOLVED.value, RequestStatus.CLOSED.value}:
        raise HTTPException(status_code=409, detail="Resolved or closed requests cannot be assigned.")

    department = db.get(Department, str(body.department_id))
    if department is None or not department.is_active:
        raise HTTPException(status_code=422, detail="Department does not exist or is inactive.")

    destinations: list[tuple[str, str]] = []
    if department.notification_email:
        destinations.append(("email", department.notification_email))
    if department.slack_channel_id:
        destinations.append(("slack", department.slack_channel_id))
    if not destinations:
        raise HTTPException(status_code=409, detail="Department has no notification destination.")

    previous_department_id = ticket.department_id
    ticket.department = department
    ticket.status = RequestStatus.ASSIGNED.value
    ticket.handled = False
    ticket.review_reason = None
    ticket.resolved_at = None
    team_message = (
        f"Support request {ticket.id} was assigned to {department.name}. "
        f"Category: {ticket.category}. Priority: {ticket.priority}. "
        f"\n\nSubject: {ticket.subject}\nMessage: {ticket.message}"
    )
    for channel, destination in destinations:
        db.add(
            _notification(
                ticket.id,
                "department_assignment",
                channel,
                destination,
                f"Support request assigned: {ticket.id}",
                team_message,
            )
        )
    db.add(
        _notification(
            ticket.id,
            "customer_status_assigned",
            "email",
            ticket.customer.email,
            f"Support request update: {ticket.id}",
            f"Your support request {ticket.id} has been assigned to our {department.name} team.",
        )
    )
    db.add(
        AuditEvent(
            support_request_id=ticket.id,
            actor_type="support_agent",
            event_type="department_assigned",
            details={
                "from_department_id": previous_department_id,
                "to_department_id": department.id,
            },
        )
    )
    db.commit()
    db.refresh(ticket)
    logger.info(
        "request_assigned id=%s department_id=%s",
        ticket.id,
        department.id,
    )
    return _result(ticket, department)
