"""Transport contracts; concrete adapters arrive in later increments."""

from dataclasses import dataclass
from typing import Protocol

from service_assistant.domain.contracts import ChatResponse


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    external_user_id: str
    external_chat_id: str
    external_message_id: str
    text: str


class ChannelAdapter(Protocol):
    async def normalize(self, event: object) -> InboundMessage: ...

    async def deliver(self, destination: str, reply: ChatResponse) -> str: ...
