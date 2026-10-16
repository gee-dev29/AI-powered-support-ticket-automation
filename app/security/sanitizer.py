import re
from typing import Any

# Regex patterns for sensitive information
CREDIT_CARD_REGEX = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
BEARER_TOKEN_REGEX = re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]+)", re.IGNORECASE)
PASSWORD_FIELD_REGEX = re.compile(r"(password|secret|token|api_key|auth)\s*[:=]\s*['\"]?([^'\"\s,]+)", re.IGNORECASE)


def redact_sensitive_text(text: str) -> str:
    """Mask credit cards, SSNs, bearer tokens, and password patterns from text."""
    if not text:
        return text

    # Redact credit cards
    redacted = CREDIT_CARD_REGEX.sub("[REDACTED_CARD]", text)
    # Redact SSNs
    redacted = SSN_REGEX.sub("[REDACTED_SSN]", redacted)
    # Redact Authorization Bearer tokens
    redacted = BEARER_TOKEN_REGEX.sub("Bearer [REDACTED_TOKEN]", redacted)
    # Redact passwords/secrets
    redacted = PASSWORD_FIELD_REGEX.sub(r"\1: [REDACTED]", redacted)
    return redacted


def sanitize_payload_for_logging(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized copy of a payload dictionary safe for logging/metrics."""
    sanitized: dict[str, Any] = {}
    sensitive_keys = {
        "password",
        "secret",
        "token",
        "api_key",
        "authorization",
        "credit_card",
        "card_number",
        "cvv",
    }

    for key, value in payload.items():
        if any(s in key.lower() for s in sensitive_keys):
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, str):
            sanitized[key] = redact_sensitive_text(value)
        elif isinstance(value, dict):
            sanitized[key] = sanitize_payload_for_logging(value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_payload_for_logging(v) if isinstance(v, dict)
                else redact_sensitive_text(v) if isinstance(v, str)
                else v
                for v in value
            ]
        else:
            sanitized[key] = value

    return sanitized
