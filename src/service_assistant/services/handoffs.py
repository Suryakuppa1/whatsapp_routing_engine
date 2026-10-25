from typing import Protocol

from service_assistant.domain.contracts import AssistedHandoff, VerifiedLink

APCPDCL_PAYMENT_URL = "https://payments.billdesk.com/MercOnline/CPDCLAPPGController"


def handoff_decision(message: str) -> str:
    answer = message.strip().casefold()
    if answer in {"yes", "y", "continue", "proceed"}:
        return "accept"
    if answer in {"no", "n", "cancel", "stop"}:
        return "decline"
    return "unknown"


class HandoffAdapter(Protocol):
    def supports(self, link: VerifiedLink) -> bool: ...
    def instructions(self, link: VerifiedLink, confirmed: bool) -> AssistedHandoff: ...


class APCPDCLBillDeskHandoff:
    """Instructions only: never opens a session, fills a form, or submits a payment."""

    def supports(self, link: VerifiedLink) -> bool:
        return link.url == APCPDCL_PAYMENT_URL and link.provider == "APCPDCL"

    def instructions(self, link: VerifiedLink, confirmed: bool) -> AssistedHandoff:
        if not self.supports(link):
            raise ValueError("The APCPDCL adapter requires its exact reviewed BillDesk destination")
        return AssistedHandoff(
            service_id=link.service_id,
            status="ready_for_user" if confirmed else "awaiting_confirmation",
            user_steps=(
                "Open the official APCPDCL BillDesk page.",
                "Enter your service number directly on that page.",
                "Complete the CAPTCHA yourself on the official page.",
                "Review the bill and amount there before choosing whether to pay.",
                "Enter payment details and authorize payment only on the official checkout.",
            ),
        )


def default_handoff_adapters() -> dict[str, HandoffAdapter]:
    return {"apcpdcl_billdesk": APCPDCLBillDeskHandoff()}
