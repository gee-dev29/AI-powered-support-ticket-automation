import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.model import AuditEvent, Customer, Department, Notification, RoutingRule, SupportRequest
from app.policy import classify_request


@pytest.fixture()
def database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with session_factory.begin() as db:
        for category, name in (
            ("billing", "Billing"),
            ("account", "Account Support"),
            ("technical", "Technical Support"),
            ("security", "Security"),
            ("general", "Support Triage"),
        ):
            department = Department(
                name=name,
                notification_email=f"{category}@example.com",
                slack_channel_id=f"C-{category}",
                is_active=True,
            )
            db.add(department)
            db.flush()
            db.add(
                RoutingRule(
                    category=category,
                    department_id=department.id,
                    is_active=True,
                )
            )
    yield session_factory
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def client(database):
    return TestClient(app)


def request_body(**updates):
    data = {
        "contact_name": "Casey Customer",
        "contact_email": "casey@example.com",
        "subject": "Invoice charged twice",
        "message": "I was charged twice for my monthly invoice.",
    }
    data.update(updates)
    return data


def test_valid_request_is_saved_and_routed_with_customer_ack(client, database):
    response = client.post(
        "/requests",
        json=request_body(),
        headers={"Idempotency-Key": "form-submit-1"},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["category"] == "billing"
    assert result["priority"] == "normal"
    assert result["status"] == "assigned"
    assert result["department"] == "Billing"
    assert result["handled"] is False

    with database() as db:
        ticket = db.get(SupportRequest, result["id"])
        assert ticket is not None
        assert ticket.idempotency_key == "form-submit-1"
        assert ticket.handled is False
        assert db.scalar(select(func.count(Notification.id))) == 3
        assert db.scalar(select(func.count(AuditEvent.id))) == 1


def test_duplicate_delivery_returns_existing_request_without_extra_notifications(client, database):
    headers = {"Idempotency-Key": "duplicate-submit"}
    first = client.post("/requests", json=request_body(), headers=headers)
    second = client.post(
        "/requests",
        json=request_body(subject="A different subject", contact_email="other@example.com"),
        headers=headers,
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["duplicate"] is True
    with database() as db:
        assert db.scalar(select(func.count(SupportRequest.id))) == 1
        assert db.scalar(select(func.count(Notification.id))) == 3
        assert db.scalar(select(func.count(Customer.id))) == 1


@pytest.mark.parametrize(
    ("subject", "message", "category", "priority"),
    [
        ("Security incident", "We found an unauthorized access to our account.", "security", "urgent"),
        ("Login issue", "I cannot sign in to my account.", "account", "normal"),
    ],
)
def test_policy_assigns_supported_categories_and_explicit_urgency(
    subject, message, category, priority
):
    decision = classify_request(subject, message)

    assert decision.category == category
    assert decision.priority == priority
    assert decision.needs_human_review is False


def test_unknown_or_ambiguous_request_is_saved_for_human_review(client, database):
    response = client.post(
        "/requests",
        json=request_body(subject="A question", message="Please explain the product options."),
        headers={"Idempotency-Key": "unclassified-submit"},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["category"] == "general"
    assert result["status"] == "needs_review"
    assert result["department"] == "Support Triage"
    assert result["review_reason"] == "unclassified_request"
    with database() as db:
        assert db.scalar(select(func.count(Notification.id))) == 3


def test_known_category_without_active_route_falls_back_to_triage(client, database):
    with database.begin() as db:
        billing = db.scalar(select(Department).where(Department.name == "Billing"))
        billing.is_active = False

    response = client.post(
        "/requests",
        json=request_body(),
        headers={"Idempotency-Key": "route-to-triage"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "needs_review"
    assert response.json()["department"] == "Support Triage"
    assert response.json()["review_reason"] == "no_active_department_route"


def test_category_overlap_requires_human_review():
    decision = classify_request(
        "Account and billing question",
        "I cannot log in and I also have a question about my invoice.",
    )

    assert decision.category == "general"
    assert decision.needs_human_review is True
    assert decision.review_reason == "ambiguous_category"


def test_missing_fields_and_invalid_idempotency_key_are_rejected(client):
    missing = client.post(
        "/requests",
        json={"contact_email": "casey@example.com"},
        headers={"Idempotency-Key": "missing-fields"},
    )
    invalid_key = client.post(
        "/requests",
        json=request_body(),
        headers={"Idempotency-Key": "not safe!"},
    )

    assert missing.status_code == 422
    assert invalid_key.status_code == 422


def test_resolution_updates_handled_and_creates_customer_notice(client, database, monkeypatch):
    monkeypatch.setenv("SUPPORT_ADMIN_API_KEY", "test-only-secret")
    created = client.post(
        "/requests",
        json=request_body(),
        headers={"Idempotency-Key": "resolve-me"},
    )
    response = client.patch(
        f"/requests/{created.json()['id']}/status",
        json={"status": "resolved"},
        headers={"Authorization": "Bearer test-only-secret"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    assert response.json()["handled"] is True
    with database() as db:
        ticket = db.get(SupportRequest, created.json()["id"])
        assert ticket.handled is True
        assert ticket.resolved_at is not None
        assert db.scalar(select(func.count(AuditEvent.id))) == 2
        assert db.scalar(select(func.count(Notification.id))) == 4


def test_status_changes_require_support_key_and_reject_invalid_transition(client, database, monkeypatch):
    monkeypatch.setenv("SUPPORT_ADMIN_API_KEY", "test-only-secret")
    created = client.post(
        "/requests",
        json=request_body(),
        headers={"Idempotency-Key": "status-protection"},
    )
    request_id = uuid.UUID(created.json()["id"])

    unauthorized = client.patch(
        f"/requests/{request_id}/status",
        json={"status": "resolved"},
    )
    invalid = client.patch(
        f"/requests/{request_id}/status",
        json={"status": "needs_review"},
        headers={"Authorization": "Bearer test-only-secret"},
    )

    assert unauthorized.status_code == 401
    assert invalid.status_code == 409


def test_support_agent_can_assign_a_review_request(client, database, monkeypatch):
    monkeypatch.setenv("SUPPORT_ADMIN_API_KEY", "test-only-secret")
    created = client.post(
        "/requests",
        json=request_body(subject="General question", message="Please explain the service options."),
        headers={"Idempotency-Key": "assign-review"},
    )
    with database() as db:
        department = db.scalar(select(Department).where(Department.name == "Technical Support"))
        department_id = department.id

    response = client.post(
        f"/requests/{created.json()['id']}/assignment",
        json={"department_id": department_id},
        headers={"Authorization": "Bearer test-only-secret"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "assigned"
    assert response.json()["department"] == "Technical Support"
    with database() as db:
        ticket = db.get(SupportRequest, created.json()["id"])
        assert ticket.department_id == department_id
        assert ticket.review_reason is None
        assert db.scalar(select(func.count(AuditEvent.id))) == 2
        assert db.scalar(select(func.count(Notification.id))) == 6
