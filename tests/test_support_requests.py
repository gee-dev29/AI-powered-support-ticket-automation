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
    assert result["priority"] == "urgent"
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


def test_llm_classification_success_path():
    from app.llm import LLMClassification

    mock_llm = LLMClassification(
        category="billing",
        priority="urgent",
        confidence=0.95,
        summary="Customer asking about payment method update",
        reasoning="Involves payment settings and cards",
    )
    decision = classify_request(
        "Update card",
        "How can I change my credit card details?",
        override_llm_result=mock_llm,
    )
    assert decision.category == "billing"
    assert decision.priority == "urgent"
    assert decision.needs_human_review is False
    assert decision.llm_used is True
    assert decision.summary == "Customer asking about payment method update"


def test_llm_low_confidence_forces_review():
    from app.llm import LLMClassification

    mock_llm = LLMClassification(
        category="technical",
        priority="normal",
        confidence=0.55,
        summary="Ambiguous question",
        reasoning="Unsure about intent",
    )
    decision = classify_request(
        "Widget issue",
        "Something feels weird with the system.",
        override_llm_result=mock_llm,
    )
    assert decision.category == "technical"
    assert decision.needs_human_review is True
    assert decision.review_reason == "low_model_confidence"


def test_deterministic_security_override_over_llm():
    from app.llm import LLMClassification

    # Even if LLM hallucinates category='account' and priority='normal'
    mock_llm = LLMClassification(
        category="account",
        priority="normal",
        confidence=0.99,
        summary="User says account was compromised",
        reasoning="User mentions credentials",
    )
    decision = classify_request(
        "Emergency: account compromised",
        "There is an unauthorized access and data breach on our system!",
        override_llm_result=mock_llm,
    )
    assert decision.category == "security"
    assert decision.priority == "urgent"


def test_llm_failure_falls_back_to_keywords():
    decision = classify_request(
        "My invoice is incorrect",
        "Please refund the incorrect charged amount.",
        override_llm_result=None,
        use_llm=False,
    )
    assert decision.category == "billing"
    assert decision.priority == "urgent"
    assert decision.needs_human_review is False
    assert decision.llm_used is False


def test_list_departments(client, database, monkeypatch):
    monkeypatch.setenv("SUPPORT_ADMIN_API_KEY", "test-secret")
    response = client.get(
        "/departments",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert response.status_code == 200
    depts = response.json()
    assert len(depts) >= 5
    dept_names = [d["name"] for d in depts]
    assert "Billing" in dept_names
    assert "Technical Support" in dept_names


def test_list_and_get_requests(client, database, monkeypatch):
    monkeypatch.setenv("SUPPORT_ADMIN_API_KEY", "test-secret")

    # Create two requests
    r1 = client.post(
        "/requests",
        json=request_body(subject="Billing query 1"),
        headers={"Idempotency-Key": "req-list-1"},
    )
    assert r1.status_code == 201
    r1_id = r1.json()["id"]

    r2 = client.post(
        "/requests",
        json=request_body(
            subject="Security incident detected",
            message="We detected a security incident and account compromised.",
        ),
        headers={"Idempotency-Key": "req-list-2"},
    )
    assert r2.status_code == 201

    # List all requests
    list_resp = client.get(
        "/requests",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] >= 2
    assert len(data["items"]) >= 2

    # Filter by priority
    urgent_resp = client.get(
        "/requests?priority=urgent",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert urgent_resp.status_code == 200
    urgent_data = urgent_resp.json()
    assert all(item["priority"] == "urgent" for item in urgent_data["items"])

    # Get single request
    detail_resp = client.get(
        f"/requests/{r1_id}",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == r1_id
    assert detail["subject"] == "Billing query 1"
    assert detail["contact_email"] == "casey@example.com"


def test_unrouted_review_creates_fallback_triage_notification(client, database, monkeypatch):
    monkeypatch.setenv("TRIAGE_EMAIL", "emergency-triage@example.com")

    # Deactivate all departments so routing fails completely
    with database() as db:
        for d in db.scalars(select(Department)).all():
            d.is_active = False
            d.notification_email = None
            d.slack_channel_id = None
        db.commit()

    response = client.post(
        "/requests",
        json=request_body(subject="Completely unroutable request", message="Random gibberish text here."),
        headers={"Idempotency-Key": "unrouted-test-1"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "needs_review"
    assert response.json()["department"] is None

    with database() as db:
        triage_notifications = db.scalars(
            select(Notification).where(Notification.notification_type == "unassigned_triage_alert")
        ).all()
        assert len(triage_notifications) == 1
        assert triage_notifications[0].destination == "emergency-triage@example.com"


def test_verified_customer_can_read_own_ticket_but_not_others(client, database):
    from app.auth import generate_customer_token

    # Customer 1 submits ticket
    r1 = client.post(
        "/requests",
        json=request_body(contact_email="alice@example.com", subject="Question from Alice"),
        headers={"Idempotency-Key": "cust-alice-1"},
    )
    assert r1.status_code == 201
    r1_id = r1.json()["id"]

    # Customer 2 submits ticket
    r2 = client.post(
        "/requests",
        json=request_body(contact_email="bob@example.com", subject="Question from Bob"),
        headers={"Idempotency-Key": "cust-bob-1"},
    )
    assert r2.status_code == 201
    r2_id = r2.json()["id"]

    alice_token = generate_customer_token("alice@example.com")
    bob_token = generate_customer_token("bob@example.com")

    # 1. Unauthenticated reading is rejected (401)
    unauth_resp = client.get(f"/requests/{r1_id}")
    assert unauth_resp.status_code == 401

    # 2. Alice can read her own ticket
    alice_resp = client.get(
        f"/requests/{r1_id}",
        headers={"X-Customer-Token": alice_token},
    )
    assert alice_resp.status_code == 200
    assert alice_resp.json()["id"] == r1_id

    # 3. Alice CANNOT read Bob's ticket (403 Forbidden)
    forbidden_resp = client.get(
        f"/requests/{r2_id}",
        headers={"X-Customer-Token": alice_token},
    )
    assert forbidden_resp.status_code == 403

    # 4. Customer list only returns their own requests
    list_alice = client.get(
        "/requests",
        headers={"X-Customer-Token": alice_token},
    )
    assert list_alice.status_code == 200
    alice_items = list_alice.json()["items"]
    assert len(alice_items) == 1
    assert alice_items[0]["contact_email"] == "alice@example.com"


def test_abuse_honeypot_rejects_automated_bots(client):
    response = client.post(
        "/requests",
        json=request_body(website="http://spam-bot-link.ru"),
        headers={"Idempotency-Key": "bot-submit-1"},
    )
    assert response.status_code == 400
    assert "automated abuse" in response.json()["detail"].lower()


def test_sanitizer_masks_sensitive_data_in_notifications(client, database):
    # Customer provides a credit card and token in message
    response = client.post(
        "/requests",
        json=request_body(
            subject="Billing issue",
            message="My credit card 4111 2222 3333 4444 was charged. Bearer secret_token_xyz was used.",
        ),
        headers={"Idempotency-Key": "sanitizer-test-1"},
    )
    assert response.status_code == 201
    ticket_id = response.json()["id"]

    with database() as db:
        dept_notifs = db.scalars(
            select(Notification).where(
                Notification.support_request_id == ticket_id,
                Notification.notification_type.in_(["department_assignment", "human_review", "unassigned_triage_alert"]),
            )
        ).all()
        assert len(dept_notifs) >= 1
        for n in dept_notifs:
            msg = n.payload["message"]
            assert "4111 2222 3333 4444" not in msg
            assert "[REDACTED_CARD]" in msg
            assert "Bearer secret_token_xyz" not in msg
            assert "Bearer [REDACTED_TOKEN]" in msg


def test_health_monitoring_probes(client):
    # Liveness probe
    live_resp = client.get("/health/live")
    assert live_resp.status_code == 200
    assert live_resp.json()["status"] == "alive"

    # Readiness probe
    ready_resp = client.get("/health/ready")
    assert ready_resp.status_code == 200
    assert ready_resp.json()["status"] == "ready"
    assert ready_resp.json()["database"] == "connected"


def test_money_requests_always_have_urgent_priority():
    # Direct tests verifying organization priority rule that money requests are urgent
    for phrase in [
        ("Invoice query", "I have a question about the invoice."),
        ("Refund please", "Please issue a refund for my payment."),
        ("Subscription billing", "Why did my monthly subscription charge increase?"),
        ("Overcharge dispute", "There is a dispute over the charged amount."),
    ]:
        decision = classify_request(phrase[0], phrase[1], use_llm=False)
        assert decision.priority == "urgent", f"Failed for {phrase}"
