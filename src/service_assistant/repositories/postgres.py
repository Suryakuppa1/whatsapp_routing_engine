import hashlib
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from service_assistant.repositories.models import Conversation, IdempotencyRecord


class ChatRepository:
    def __init__(self, session: Session):
        self.session = session

    def lock_request(self, owner: str, key: str) -> None:
        # Stable transaction-scoped lock works across processes and replicas.
        digest = hashlib.sha256(f"{owner}\0{key}".encode()).digest()
        lock_id = int.from_bytes(digest[:8], "big", signed=True)
        self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})

    def previous(self, owner: str, key: str) -> IdempotencyRecord | None:
        return self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner == owner, IdempotencyRecord.key == key
            )
        )

    def conversation(self, owner: str, conversation_id: UUID) -> Conversation | None:
        return self.session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.owner == owner)
            .with_for_update()
        )
