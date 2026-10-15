import re
from dataclasses import dataclass


CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "security": (
        "security incident",
        "account compromised",
        "unauthorized access",
        "data breach",
        "suspicious login",
    ),
    "billing": ("invoice", "billing", "charged", "charge", "refund", "payment"),
    "account": ("account", "password", "sign in", "login", "log in", "access account"),
    "technical": ("error", "bug", "outage", "crash", "not working", "broken", "technical issue"),
}

URGENT_TERMS = (
    "security incident",
    "account compromised",
    "unauthorized access",
    "data breach",
    "service outage",
    "system-wide outage",
    "all users are affected",
)


@dataclass(frozen=True)
class TriageDecision:
    category: str
    priority: str
    needs_human_review: bool
    review_reason: str | None


def _contains_term(text: str, term: str) -> bool:
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    return re.search(rf"(?<!\w){escaped}(?!\w)", text) is not None


def classify_request(subject: str, message: str) -> TriageDecision:
    """Apply fixed keyword rules; this policy does not treat model output as authority."""
    text = f"{subject} {message}".casefold()
    matches = [
        category
        for category, terms in CATEGORY_TERMS.items()
        if any(_contains_term(text, term) for term in terms)
    ]
    if "security" in matches:
        matches = ["security"]
    urgent = any(_contains_term(text, term) for term in URGENT_TERMS)

    if len(matches) == 1:
        return TriageDecision(
            category=matches[0],
            priority="urgent" if urgent else "normal",
            needs_human_review=False,
            review_reason=None,
        )

    reason = "ambiguous_category" if len(matches) > 1 else "unclassified_request"
    return TriageDecision(
        category="general",
        priority="urgent" if urgent else "normal",
        needs_human_review=True,
        review_reason=reason,
    )
