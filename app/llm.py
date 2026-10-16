import json
import logging
import os
from typing import Literal

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("support_requests.llm")

AllowedCategory = Literal["billing", "account", "technical", "security", "general"]
AllowedPriority = Literal["normal", "urgent"]


class LLMClassification(BaseModel):
    category: AllowedCategory = Field(
        description="The most suitable department category for the ticket."
    )
    priority: AllowedPriority = Field(
        description="Priority level: urgent if security, severe outage, or major block; otherwise normal."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0.",
    )
    summary: str = Field(
        min_length=1,
        max_length=200,
        description="Concise one-line summary of the user issue.",
    )
    reasoning: str = Field(
        min_length=1,
        max_length=500,
        description="Brief justification for the assigned category and priority.",
    )


CLASSIFICATION_SYSTEM_PROMPT = """You are an expert customer support ticket classifier for an enterprise software platform.
Analyze the user's support ticket subject and message. Categorize it accurately into one of:
- 'security': data breach, compromised credentials, unauthorized access, vulnerability reports.
- 'billing': invoices, payment issues, subscription changes, refunds, charges, disputes, financial transactions.
- 'account': password reset, login problems, user permissions, profile settings.
- 'technical': software bugs, service errors, API crashes, technical performance issues.
- 'general': general inquiries, feedback, ambiguous requests not fitting the above.

Mark priority as 'urgent' if:
1. There is an active security threat, credentials breach, or data loss.
2. There is severe system downtime or critical outage affecting operations.
3. The request has to do with money (billing disputes, payment failures, incorrect charges, refunds, invoice errors).
Otherwise mark priority as 'normal'.

Respond ONLY with a valid JSON object matching the requested schema.
"""


def get_openai_client() -> OpenAI | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.getenv("OPENAI_BASE_URL")
    timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "5.0"))
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)


def classify_with_llm(
    subject: str,
    message: str,
    client: OpenAI | None = None,
) -> LLMClassification | None:
    """Call the LLM with structured output and validate with Pydantic.

    Returns None if LLM is unconfigured, times out, fails, or produces malformed output.
    """
    if client is None:
        client = get_openai_client()
    if client is None:
        logger.debug("OpenAI API key not configured; skipping LLM triage.")
        return None

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    user_content = f"Subject: {subject}\nMessage: {message}"

    try:
        response = client.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format=LLMClassification,
            temperature=0.0,
        )
        parsed = response.choices[0].message.parsed
        if parsed is not None:
            logger.info(
                "llm_classification_success category=%s priority=%s confidence=%.2f",
                parsed.category,
                parsed.priority,
                parsed.confidence,
            )
            return parsed
    except (OpenAIError, ValidationError, json.JSONDecodeError, IndexError, Exception) as exc:
        logger.warning("llm_classification_failed error=%s", str(exc))

    return None
