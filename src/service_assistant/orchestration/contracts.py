from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints

from service_assistant.domain.contracts import ConversationState, ServiceIntent

SlotValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class IntentInterpretation(BaseModel):
    """Extracted context only: no tools, links, instructions, or generated questions."""

    model_config = ConfigDict(extra="forbid", strict=True)
    intent: ServiceIntent
    provider: SlotValue | None
    jurisdiction: SlotValue | None
    service_detail: SlotValue | None
    action: Literal["information", "execute", "unknown"]


class IntentProvider(Protocol):
    def interpret(
        self, message: str, state: ConversationState, locale: str = "en-IN"
    ) -> IntentInterpretation: ...


class ProviderError(Exception):
    """Safe metadata: never attach raw requests, outputs, or upstream exceptions."""

    def __init__(self, code: str, retryable: bool):
        self.code, self.retryable = code, retryable
        super().__init__(code)
