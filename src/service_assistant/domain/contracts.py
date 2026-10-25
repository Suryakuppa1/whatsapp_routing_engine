from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=4000)
    locale: Literal["en-IN", "en", "en-US", "en-GB"] = "en-IN"


ServiceIntent = Literal["electricity", "government_form", "appointment", "service_issue", "unknown"]
SlotName = Literal["provider", "jurisdiction", "service_detail"]


class ConversationState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: ServiceIntent = "unknown"
    provider: str | None = Field(default=None, max_length=200)
    jurisdiction: str | None = Field(default=None, max_length=200)
    service_detail: str | None = Field(default=None, max_length=200)
    pending_field: SlotName | None = None


class Clarification(BaseModel):
    field: Literal["provider", "jurisdiction", "service_detail", "service"]
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


class AssistantReply(BaseModel):
    status: Literal["answered", "needs_clarification", "no_verified_match", "unsupported"]
    message: str
    clarification: Clarification | None = None
    links: list[VerifiedLink] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def enforce_evidence(self):
        if (self.status == "answered") != bool(self.links):
            raise ValueError("Only answered replies can contain verified evidence")
        if (self.status == "needs_clarification") != bool(self.clarification):
            raise ValueError("Only clarification replies can contain a question")
        return self


class ChatResponse(AssistantReply):
    request_id: UUID
    conversation_id: UUID
