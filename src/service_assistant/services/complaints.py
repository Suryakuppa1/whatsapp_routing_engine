import re
from collections.abc import Callable
from datetime import UTC, datetime

from service_assistant.domain.contracts import (
    AssistantReply,
    Clarification,
    ComplaintEmailDraft,
    ConversationState,
)
from service_assistant.services.registry import ServiceRegistry, normalize
from service_assistant.tools.verify_url import verify_url

QUESTIONS = {
    "provider": ("provider", "Which provider is the complaint for?"),
    "jurisdiction": ("jurisdiction", "Which country and state or city is the complaint for?"),
    "description": (
        "complaint_description",
        "Please describe the issue and the help you want. "
        "Don’t include passwords, OTPs, or payment details.",
    ),
    "sender_name": (
        "sender_name",
        "What name or username should I use after ‘Regards’ in the draft?",
    ),
}


def valid_sender_name(value: str) -> bool:
    return bool(re.fullmatch(r"@?[^\W\d_][\w .'-]{0,98}", value, flags=re.UNICODE))


def render_email_draft(draft: ComplaintEmailDraft) -> str:
    return (
        f"To: {draft.to}\nSubject: {draft.subject}\n\n"
        f"{draft.body}\n\n{draft.regards}\n\n"
        "This is a draft; nothing has been sent. Please review it, add any necessary "
        "service references in your email app, and send it from your own email account."
    )


class ComplaintDraftService:
    def __init__(
        self, registry: ServiceRegistry, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ):
        self.registry, self.clock = registry, clock
        self.sources = {source.id: source for source in registry.sources}

    def advance(
        self, state: ConversationState, message: str | None = None
    ) -> tuple[ConversationState, AssistantReply]:
        state = state.model_copy(deep=True)
        if message is not None and state.complaint_pending:
            if message.strip().casefold() in {"cancel", "stop", "no"}:
                return ConversationState(), AssistantReply(
                    status="cancelled", message="Stopped. No email has been sent."
                )
            field = state.complaint_pending
            value = message.strip()
            if field == "sender_name" and not valid_sender_name(value):
                return state, self._question(field)
            if field in {"provider", "jurisdiction"} and len(value) > 200:
                return state, self._question(field)
            if field == "description" and (
                len(value) < 10 or any(ord(c) < 32 and c not in "\n\t" for c in value)
            ):
                return state, self._question(field)
            attribute = {
                "description": "complaint_description",
                "sender_name": "complaint_sender_name",
            }.get(field, field)
            setattr(state, attribute, value)
            state.complaint_pending = None
        for field in ("provider", "jurisdiction"):
            if not getattr(state, field):
                state.complaint_pending = field
                return state, self._question(field)
        contacts = []
        now = self.clock()
        for contact in self.registry.support_contacts:
            source = self.sources[contact.source_id]
            if (
                contact.enabled
                and source.active(now)
                and normalize(state.provider)
                in {normalize(alias) for alias in (source.provider, *source.provider_aliases)}
                and normalize(state.jurisdiction)
                in {normalize(alias) for alias in contact.jurisdiction_aliases}
                and not verify_url(contact.evidence_url, source.hostname, source.allowed_paths)
            ):
                contacts.append((contact, source))
        if len(contacts) != 1:
            state.complaint_pending = None
            return state, AssistantReply(
                status="no_verified_match",
                message=(
                    "I couldn’t verify a unique official complaint email for that provider "
                    "and location. No email has been sent."
                ),
            )
        for field, attribute in (
            ("description", "complaint_description"),
            ("sender_name", "complaint_sender_name"),
        ):
            if not getattr(state, attribute):
                state.complaint_pending = field
                return state, self._question(field)
        contact, source = contacts[0]
        # Preserve user-reported facts as quoted text; never ask a model to invent missing details.
        description = "\n".join(f"> {line}" for line in state.complaint_description.splitlines())
        provider = " ".join(source.provider.split())
        body = (
            f"Dear {provider} Customer Support Team,\n\n"
            "I am writing to request assistance with the following issue:\n\n"
            f"{description}\n\nLocation: {state.jurisdiction}\n\n"
            "Please investigate this matter, advise on the next steps, and provide a complaint "
            "reference number and expected resolution timeline.\n\nThank you for your assistance."
        )
        draft = ComplaintEmailDraft(
            to=contact.email,
            subject=f"Service complaint - {provider}",
            body=body,
            regards=f"Regards,\n{state.complaint_sender_name}",
            sender_name=state.complaint_sender_name,
            provider=source.provider,
            contact_id=contact.id,
            source_id=source.id,
            evidence_url=contact.evidence_url,
            source_reviewed_at=source.reviewed_at,
            verified_at=now,
            registry_version=self.registry.version,
        )
        return state, AssistantReply(
            status="draft_ready",
            message=render_email_draft(draft),
            email_draft=draft,
        )

    def draft_current(self, draft: ComplaintEmailDraft | None) -> bool:
        if draft is None:
            return True
        contact = next(
            (c for c in self.registry.support_contacts if c.id == draft.contact_id), None
        )
        source = self.sources.get(draft.source_id)
        return bool(
            contact
            and source
            and contact.enabled
            and source.active(self.clock())
            and contact.source_id == source.id
            and contact.email == draft.to
            and contact.evidence_url == draft.evidence_url
            and source.provider == draft.provider
            and source.reviewed_at == draft.source_reviewed_at
            and self.registry.version == draft.registry_version
            and not verify_url(contact.evidence_url, source.hostname, source.allowed_paths)
        )

    @staticmethod
    def _question(field: str) -> AssistantReply:
        name, question = QUESTIONS[field]
        return AssistantReply(
            status="needs_clarification",
            message=question,
            clarification=Clarification(field=name, question=question),
        )
