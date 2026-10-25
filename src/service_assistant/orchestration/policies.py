import re

from service_assistant.domain.contracts import AssistantReply

# Conservative guard for obvious secrets/actions, not a general PII detector.
_SENSITIVE = re.compile(
    r"\b(otp|password|passcode|pin|cvv|credential|credit card|debit card)\b|"
    r"\b\d{4,8}\b|\b(?:\d[ -]?){13,19}\b|\bsk-[a-zA-Z0-9_-]+",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"\b(pay|submit|book|transfer)\b.*\b(for me|on my behalf|now)\b", re.IGNORECASE
)


def policy_reply(message: str) -> AssistantReply | None:
    if _SENSITIVE.search(message) or _ACTION.search(message):
        return AssistantReply(
            status="unsupported",
            message=(
                "I can help you find official service information. "
                "Please don’t share passwords, OTPs, or payment details. "
                "Complete payments, bookings, and submissions directly with the provider."
            ),
        )
    return None
