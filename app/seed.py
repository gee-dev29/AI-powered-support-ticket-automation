import os

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
import app.model  # noqa: F401
from app.model import Department, RoutingRule


DEPARTMENTS = {
    "billing": ("Billing", "BILLING_EMAIL", "BILLING_SLACK_CHANNEL"),
    "account": ("Account Support", "ACCOUNT_EMAIL", "ACCOUNT_SLACK_CHANNEL"),
    "technical": ("Technical Support", "TECHNICAL_EMAIL", "TECHNICAL_SLACK_CHANNEL"),
    "security": ("Security", "SECURITY_EMAIL", "SECURITY_SLACK_CHANNEL"),
    "general": ("Support Triage", "TRIAGE_EMAIL", "TRIAGE_SLACK_CHANNEL"),
}


def main() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal.begin() as db:
        for category, (name, email_env, slack_env) in DEPARTMENTS.items():
            email = os.getenv(email_env) or None
            slack = os.getenv(slack_env) or None
            department = db.scalar(select(Department).where(Department.name == name))
            if department is None:
                department = Department(name=name)
                db.add(department)
                db.flush()
            department.notification_email = email
            department.slack_channel_id = slack
            department.is_active = bool(email or slack)

            rule = db.scalar(select(RoutingRule).where(RoutingRule.category == category))
            if rule is None:
                rule = RoutingRule(category=category, department_id=department.id)
                db.add(rule)
            rule.department = department
            rule.is_active = bool(email or slack)

    print("Department routing seeded. Routes without a configured channel remain inactive.")


if __name__ == "__main__":
    main()
