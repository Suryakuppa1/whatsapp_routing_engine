from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=4000)
    locale: Literal["en-IN", "en", "en-US", "en-GB"] = "en-IN"


ServiceIntent = Literal[
    "electricity", "government_form", "appointment", "service_issue", "complaint_email", "unknown"
]
SlotName = Literal["provider", "jurisdiction", "service_detail"]


class ConversationState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: ServiceIntent = "unknown"
    provider: str | None = Field(default=None, max_length=200)
    jurisdiction: str | None = Field(default=None, max_length=200)
    service_detail: str | None = Field(default=None, max_length=200)
    pending_field: SlotName | None = None
    pending_handoff_service_id: str | None = None
    pending_handoff_registry_version: str | None = None
    complaint_description: str | None = Field(default=None, max_length=4000)
    complaint_sender_name: str | None = Field(default=None, max_length=100)
    complaint_pending: Literal["provider", "jurisdiction", "description", "sender_name"] | None = (
        None
    )


class Clarification(BaseModel):
    field: Literal[
        "provider",
        "jurisdiction",
        "service_detail",
        "service",
        "handoff_confirmation",
        "complaint_description",
        "sender_name",
    ]
    question: str


class VerifiedLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    label: str
    url: str
    provider: str
    service_id: str
    source_id: str
    verification: Literal["registry_verified"] = "registry_verified"
    verified_at: AwareDatetime
    source_reviewed_at: AwareDatetime
    registry_version: str
    reachability: Literal["not_checked"] = "not_checked"
    discovery_method: Literal["registry", "approved_search"] = "registry"


class AssistedHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    service_id: str
    mode: Literal["manual_official_site"] = "manual_official_site"
    status: Literal["awaiting_confirmation", "ready_for_user", "declined"]
    automatic_prefill: Literal[False] = False
    payment_by: Literal["user_on_official_site"] = "user_on_official_site"
    user_steps: tuple[str, ...] = ()


class ComplaintEmailDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    to: str
    subject: str
    body: str
    regards: str
    sender_name: str
    provider: str
    contact_id: str
    source_id: str
    evidence_url: str
    source_reviewed_at: AwareDatetime
    verified_at: AwareDatetime
    registry_version: str
    verification: Literal["registry_verified"] = "registry_verified"
    delivery_status: Literal["not_sent"] = "not_sent"
    user_action: Literal["review_and_send_from_your_email"] = "review_and_send_from_your_email"


class AssistantReply(BaseModel):
    status: Literal[
        "answered",
        "needs_clarification",
        "no_verified_match",
        "unsupported",
        "cancelled",
        "draft_ready",
    ]
    message: str
    clarification: Clarification | None = None
    links: list[VerifiedLink] = Field(default_factory=list, max_length=3)
    handoff: AssistedHandoff | None = None
    email_draft: ComplaintEmailDraft | None = None

    @model_validator(mode="after")
    def enforce_evidence(self):
        if (self.status == "draft_ready") != bool(self.email_draft):
            raise ValueError("Draft-ready replies require an unsent email draft")
        if (self.status == "answered") != bool(self.links):
            raise ValueError("Only answered replies can contain verified evidence")
        if (self.status == "needs_clarification") != bool(self.clarification):
            raise ValueError("Only clarification replies can contain a question")
        if self.handoff:
            expected = {
                "awaiting_confirmation": "needs_clarification",
                "ready_for_user": "answered",
                "declined": "cancelled",
            }[self.handoff.status]
            if self.status != expected:
                raise ValueError("Handoff status must match the response outcome")
            if self.handoff.status == "ready_for_user" and not any(
                link.service_id == self.handoff.service_id for link in self.links
            ):
                raise ValueError("Ready handoffs require matching service evidence")
        return self


class ChatResponse(AssistantReply):
    request_id: UUID
    conversation_id: UUID
