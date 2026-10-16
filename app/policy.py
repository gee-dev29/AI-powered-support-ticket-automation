import logging
import re
from dataclasses import dataclass

from app.llm import LLMClassification, classify_with_llm

logger = logging.getLogger("support_requests.policy")

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

# Organization-approved priority policy: financial/money-related requests receive urgent priority
MONEY_TERMS = (
    "invoice",
    "billing",
    "charged",
    "charge",
    "refund",
    "payment",
    "money",
    "credit card",
    "bank",
    "wire",
    "pricing",
    "fee",
    "dispute",
    "transaction",
    "subscription",
    "cost",
    "overcharge",
)


@dataclass(frozen=True)
class TriageDecision:
    category: str
    priority: str
    needs_human_review: bool
    review_reason: str | None
    llm_used: bool = False
    summary: str | None = None


def _contains_term(text: str, term: str) -> bool:
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    return re.search(rf"(?<!\w){escaped}(?!\w)", text) is not None


def classify_request(
    subject: str,
    message: str,
    use_llm: bool = True,
    override_llm_result: LLMClassification | None = None,
) -> TriageDecision:
    """Triage incoming request using LLM structured output with deterministic guardrails.

    Policy:
    1. Deterministic rules guard critical issues:
       - Explicit security keywords force 'security' category and 'urgent' priority.
       - Financial and money-related requests receive 'urgent' priority.
    2. An LLM performs structured classification if enabled and available.
    3. Low LLM confidence (<0.70) or 'general' categorization forces human review.
    4. Deterministic keyword heuristics serve as fallback if LLM is unavailable or fails.
    """
    text = f"{subject} {message}".casefold()
    deterministic_urgent = any(_contains_term(text, term) for term in URGENT_TERMS)
    has_security_breach_terms = any(
        _contains_term(text, term) for term in CATEGORY_TERMS["security"]
    )
    has_money_terms = any(_contains_term(text, term) for term in MONEY_TERMS)
    is_urgent_topic = deterministic_urgent or has_security_breach_terms or has_money_terms

    llm_result: LLMClassification | None = override_llm_result
    if llm_result is None and use_llm:
        llm_result = classify_with_llm(subject, message)

    if llm_result is not None:
        category = "security" if has_security_breach_terms else llm_result.category
        priority = (
            "urgent"
            if (
                is_urgent_topic
                or llm_result.priority == "urgent"
                or category in ("security", "billing")
            )
            else "normal"
        )

        needs_review = False
        review_reason = None

        if llm_result.confidence < 0.70:
            needs_review = True
            review_reason = "low_model_confidence"
        elif category == "general":
            needs_review = True
            review_reason = "general_inquiry"

        return TriageDecision(
            category=category,
            priority=priority,
            needs_human_review=needs_review,
            review_reason=review_reason,
            llm_used=True,
            summary=llm_result.summary,
        )

    # Fallback deterministic keyword matcher
    matches = [
        cat
        for cat, terms in CATEGORY_TERMS.items()
        if any(_contains_term(text, term) for term in terms)
    ]
    if "security" in matches or has_security_breach_terms:
        matches = ["security"]

    if len(matches) == 1:
        cat = matches[0]
        is_urgent = is_urgent_topic or cat in ("security", "billing")
        return TriageDecision(
            category=cat,
            priority="urgent" if is_urgent else "normal",
            needs_human_review=False,
            review_reason=None,
            llm_used=False,
            summary=None,
        )

    reason = "ambiguous_category" if len(matches) > 1 else "unclassified_request"
    return TriageDecision(
        category="general",
        priority="urgent" if is_urgent_topic else "normal",
        needs_human_review=True,
        review_reason=reason,
        llm_used=False,
        summary=None,
    )
