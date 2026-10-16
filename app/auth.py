import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Query, status

ActorRole = Literal["admin", "agent", "customer"]


@dataclass(frozen=True)
class Actor:
    actor_type: Literal["staff", "customer"]
    role: ActorRole
    identifier: str  # username, key label, or customer email


def get_customer_auth_secret() -> str:
    return os.getenv("CUSTOMER_AUTH_SECRET", "dev-customer-secret-key-change-in-prod")


def generate_customer_token(email: str, expires_in_seconds: int = 86400 * 30) -> str:
    """Generate a tamper-proof signed customer token for verified request access."""
    expiry = int(time.time()) + expires_in_seconds
    data = f"{email.strip().lower()}:{expiry}"
    signature = hmac.new(
        get_customer_auth_secret().encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{data}:{signature}"


def verify_customer_token(token: str) -> str | None:
    """Validate customer token and return the verified email if valid, or None."""
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        email, expiry_str, signature = parts
        expiry = int(expiry_str)
        if time.time() > expiry:
            return None
        expected_sig = hmac.new(
            get_customer_auth_secret().encode("utf-8"),
            f"{email}:{expiry}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        return email
    except Exception:
        return None


def get_current_actor(
    authorization: Annotated[str | None, Header()] = None,
    x_customer_token: Annotated[str | None, Header()] = None,
    customer_token: Annotated[str | None, Query()] = None,
) -> Actor:
    """Authenticate either staff (Bearer token) or verified customer (token)."""
    # 1. Staff Authentication
    if authorization:
        token = authorization.removeprefix("Bearer ").strip()
        admin_key = os.getenv("SUPPORT_ADMIN_API_KEY")
        if admin_key and hmac.compare_digest(token, admin_key):
            return Actor(actor_type="staff", role="admin", identifier="admin")

        agent_keys_raw = os.getenv("SUPPORT_AGENT_API_KEYS", "")
        agent_keys = [k.strip() for k in agent_keys_raw.split(",") if k.strip()]
        for idx, key in enumerate(agent_keys):
            if hmac.compare_digest(token, key):
                return Actor(actor_type="staff", role="agent", identifier=f"agent-{idx + 1}")

    # 2. Customer Authentication
    cust_token = x_customer_token or customer_token
    if cust_token:
        verified_email = verify_customer_token(cust_token)
        if verified_email:
            return Actor(actor_type="customer", role="customer", identifier=verified_email)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide a valid staff Bearer token or customer token.",
    )


def require_staff_actor(
    actor: Annotated[Actor, Depends(get_current_actor)],
) -> Actor:
    """Authorize staff members (admin or agent)."""
    if actor.actor_type != "staff":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff access required.",
        )
    return actor


def require_admin_actor(
    actor: Annotated[Actor, Depends(require_staff_actor)],
) -> Actor:
    """Authorize administrator role only."""
    if actor.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return actor

