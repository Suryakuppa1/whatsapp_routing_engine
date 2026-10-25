import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from service_assistant.domain.contracts import ChatRequest, ChatResponse, ConversationState
from service_assistant.orchestration.engine import Orchestrator
from service_assistant.orchestration.policies import policy_reply
from service_assistant.repositories.models import Conversation, IdempotencyRecord, Message
from service_assistant.repositories.postgres import ChatRepository
from service_assistant.services.handoffs import handoff_decision

logger = logging.getLogger("service_assistant.orchestration")


class ApplicationError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


class ChatService:
    def __init__(self, session: Session, engine: Orchestrator, ttl: int):
        self.session, self.engine, self.ttl = session, engine, ttl
        self.repository = ChatRepository(session)

    def chat(self, owner: str, key: str, request: ChatRequest, request_id: UUID) -> ChatResponse:
        payload = request.model_dump_json()
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        self.repository.lock_request(owner, key)
        now = datetime.now(UTC)
        previous = self.repository.previous(owner, key)
        if previous and previous.expires_at > now:
            if previous.request_hash != fingerprint:
                raise ApplicationError(409, "IDEMPOTENCY_CONFLICT", "Use a new idempotency key.")
            cached = ChatResponse.model_validate(previous.response)
            if not self.engine.discovery.links_current(cached.links):
                raise ApplicationError(
                    409,
                    "VERIFICATION_EXPIRED",
                    "The service evidence has changed. Please retry with a new idempotency key.",
                )
            return cached
        if previous:
            self.session.delete(previous)
            self.session.flush()

        if request.conversation_id:
            conversation = self.repository.conversation(owner, request.conversation_id)
            if not conversation:
                raise ApplicationError(404, "CONVERSATION_NOT_FOUND", "Conversation not found.")
            if conversation.expires_at <= datetime.now(UTC):
                raise ApplicationError(410, "CONVERSATION_EXPIRED", "Start a new conversation.")
        else:
            conversation = Conversation(owner=owner, state={}, expires_at=now)
            self.session.add(conversation)
            self.session.flush()

        state = ConversationState.model_validate(conversation.state)
        reply = policy_reply(request.message)
        blocked = reply is not None
        withhold_handoff_text = (
            state.pending_handoff_service_id is not None
            and handoff_decision(request.message) == "unknown"
        )
        if reply is None:
            started = time.monotonic()
            state, reply = self.engine.reply(request.message, state, request.locale)
            logger.info(
                json.dumps(
                    {
                        "event": "intent_interpreted",
                        "request_id": str(request_id),
                        "intent": state.intent,
                        "status": reply.status,
                        "provider": type(self.engine.provider).__name__,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    }
                )
            )
        conversation.state = state.model_dump()
        conversation.expires_at = datetime.now(UTC) + timedelta(seconds=self.ttl)
        response = ChatResponse(
            **reply.model_dump(), request_id=request_id, conversation_id=conversation.id
        )
        self.session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    text=(
                        "[sensitive or action request withheld]"
                        if blocked
                        else "[unexpected handoff response withheld]"
                        if withhold_handoff_text
                        else request.message
                    ),
                ),
                Message(conversation_id=conversation.id, role="assistant", text=reply.message),
                IdempotencyRecord(
                    owner=owner,
                    key=key,
                    request_hash=fingerprint,
                    response=response.model_dump(mode="json"),
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                ),
            ]
        )
        return response
