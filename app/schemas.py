import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.model.request_status import RequestStatus


class SupportRequestCreate(BaseModel):
    contact_name: str = Field(min_length=1, max_length=160)
    contact_email: EmailStr
    subject: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=10, max_length=10_000)

    @field_validator("contact_name", "subject", "message")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("contact_email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class SupportRequestResult(BaseModel):
    id: UUID
    category: str
    priority: str
    status: RequestStatus
    handled: bool
    department: str | None
    review_reason: str | None
    duplicate: bool = False


class SupportRequestDetail(SupportRequestResult):
    subject: str
    message: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    contact_name: str
    contact_email: str


class SupportRequestList(BaseModel):
    total: int
    items: list[SupportRequestDetail]


class StatusUpdate(BaseModel):
    status: RequestStatus


class DepartmentAssignment(BaseModel):
    department_id: UUID


class DepartmentRead(BaseModel):
    id: UUID
    name: str
    notification_email: str | None
    slack_channel_id: str | None
    is_active: bool


class HealthResponse(BaseModel):
    status: str


def valid_idempotency_key(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 200 or not re.fullmatch(r"[\w.:/@+-]+", value):
        raise ValueError("Idempotency-Key must be 1-200 safe characters.")
    return value
