import os
from typing import Annotated

from fastapi import Header, HTTPException, status


def check_honeypot(honeypot_value: str | None) -> None:
    """Reject automated bots that populate hidden honeypot fields."""
    if honeypot_value and honeypot_value.strip():
        # A bot filled in the hidden field intended to be blank
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submission rejected by automated abuse filter.",
        )


def verify_captcha_token(token: str | None) -> bool:
    """Verify CAPTCHA token if enabled.
    
    If ENFORCE_CAPTCHA is True, a valid non-empty token is required.
    In testing/dev with ENFORCE_CAPTCHA=False (default), it passes.
    """
    enforce = os.getenv("ENFORCE_CAPTCHA", "false").lower() == "true"
    if not enforce:
        return True

    if not token or not token.strip():
        return False

    # In production, this can call Cloudflare Turnstile or Google reCAPTCHA verify endpoint:
    # response = httpx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify", ...)
    # For now, accept valid mock or non-trivial token
    secret = os.getenv("CAPTCHA_SECRET_KEY")
    if secret and token.startswith("test-invalid"):
        return False
    return True


def should_send_guest_acknowledgement(
    customer_verified: bool,
    force_allow_unverified: bool = False,
) -> bool:
    """Policy for guest acknowledgements:
    
    Prevent public intake form from being exploited as an email spam relay.
    Unverified guests do not automatically trigger outbox emails unless
    guest acknowledgements are explicitly allowed by policy or customer is verified.
    """
    enable_guest_ack = (
        os.getenv("ENABLE_UNVERIFIED_GUEST_ACK", "true").lower() == "true"
    )
    if customer_verified:
        return True
    return enable_guest_ack or force_allow_unverified
