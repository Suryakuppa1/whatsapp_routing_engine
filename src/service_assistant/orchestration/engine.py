from pydantic import ValidationError

from service_assistant.domain.contracts import (
    AssistantReply,
    AssistedHandoff,
    Clarification,
    ConversationState,
)
from service_assistant.orchestration.contracts import (
    IntentInterpretation,
    IntentProvider,
    ProviderError,
)
from service_assistant.orchestration.policies import policy_reply
from service_assistant.services.discovery import ServiceDiscovery
from service_assistant.services.handoffs import handoff_decision
from service_assistant.services.plugins import DEFAULT_PLUGINS, ServicePlugin, plugin_map
from service_assistant.services.registry import ServiceRegistry

QUESTIONS = {
    "provider": "Which provider or organization is this for?",
    "jurisdiction": "Which country and state or city is this service for?",
    "service_detail": "Which form, appointment, or service issue do you need help with?",
    "service": "Which service do you need help finding?",
}


class FakeIntentProvider:
    """Deterministic development fixture; this is not an LLM implementation."""

    def interpret(
        self, message: str, state: ConversationState, locale: str = "en-IN"
    ) -> IntentInterpretation:
        context_fields = {"intent", "provider", "jurisdiction", "service_detail"}
        values = state.model_dump(include=context_fields)
        if "electricity" in message.casefold():
            if state.intent != "electricity":
                values = ConversationState(intent="electricity").model_dump(include=context_fields)
        elif state.pending_field:
            values[state.pending_field] = message[:200]
        else:
            values = ConversationState().model_dump(include=context_fields)
        return IntentInterpretation(**values, action="information")


class Orchestrator:
    def __init__(
        self,
        provider: IntentProvider,
        discovery: ServiceDiscovery | None = None,
        plugins: tuple[ServicePlugin, ...] = DEFAULT_PLUGINS,
    ):
        self.provider = provider
        self.discovery = discovery or ServiceDiscovery(ServiceRegistry(version="empty-v1"))
        self.plugins = plugin_map(plugins)

    def reply(
        self, message: str, state: ConversationState, locale: str = "en-IN"
    ) -> tuple[ConversationState, AssistantReply]:
        if state.pending_handoff_service_id:
            return self._confirm_handoff(message, state)
        result = self.provider.interpret(message, state.model_copy(deep=True), locale)
        if not isinstance(result, IntentInterpretation):
            raise ProviderError("PROVIDER_INVALID_OUTPUT", False)
        try:
            result = IntentInterpretation.model_validate(result.model_dump())
        except ValidationError:
            raise ProviderError("PROVIDER_INVALID_OUTPUT", False) from None
        normalized_message = " ".join(message.casefold().split())
        for field in ("provider", "jurisdiction", "service_detail"):
            value = getattr(result, field)
            if value and ("://" in value or "www." in value.casefold() or policy_reply(value)):
                raise ProviderError("PROVIDER_INVALID_OUTPUT", False)
            if value:
                normalized_value = " ".join(value.casefold().split())
                previous = getattr(state, field) if result.intent == state.intent else None
                if normalized_value not in normalized_message and value != previous:
                    raise ProviderError("PROVIDER_UNGROUNDED_OUTPUT", False)
        if result.action == "execute":
            return state, AssistantReply(
                status="unsupported",
                message="I can help find official information. Complete actions with the provider.",
            )
        if result.action == "unknown":
            return state, self._clarify("service")
        # Results contain complete context. Nulls clear corrected or switched slots.
        state = ConversationState(
            intent=result.intent,
            provider=result.provider,
            jurisdiction=result.jurisdiction,
            service_detail=result.service_detail,
        )
        if state.intent == "unknown":
            return ConversationState(), self._clarify("service")
        plugin = self.plugins.get(state.intent)
        if plugin is None:
            return state, AssistantReply(
                status="unsupported", message="This service category is not supported yet."
            )
        for field in plugin.required_fields:
            if getattr(state, field) is None:
                state.pending_field = field
                return state, self._clarify(field)
        reply = self.discovery.discover(state)
        if reply.handoff and reply.handoff.status == "awaiting_confirmation":
            state.pending_handoff_service_id = reply.handoff.service_id
            state.pending_handoff_registry_version = self.discovery.registry.version
        elif reply.clarification and reply.clarification.field != "service":
            state.pending_field = reply.clarification.field
        return state, reply

    def _confirm_handoff(
        self, message: str, state: ConversationState
    ) -> tuple[ConversationState, AssistantReply]:
        answer = handoff_decision(message)
        service_id = state.pending_handoff_service_id
        if answer == "decline":
            state = state.model_copy(
                update={
                    "pending_handoff_service_id": None,
                    "pending_handoff_registry_version": None,
                }
            )
            return state, AssistantReply(
                status="cancelled",
                message="Stopped. The assistant has not submitted a form or initiated a payment.",
                handoff=AssistedHandoff(service_id=service_id, status="declined"),
            )
        if answer != "accept":
            return state, AssistantReply(
                status="needs_clarification",
                message="Please reply yes for the official link, or no to stop.",
                clarification=Clarification(
                    field="handoff_confirmation", question="Would you like the official link?"
                ),
                handoff=AssistedHandoff(service_id=service_id, status="awaiting_confirmation"),
            )
        version = state.pending_handoff_registry_version
        state = state.model_copy(
            update={
                "pending_handoff_service_id": None,
                "pending_handoff_registry_version": None,
            }
        )
        if version != self.discovery.registry.version:
            return state, AssistantReply(
                status="no_verified_match",
                message="The service information changed. Please start a new service request.",
            )
        return state, self.discovery.discover(state, confirmed_service_id=service_id)

    @staticmethod
    def _clarify(field: str) -> AssistantReply:
        question = QUESTIONS[field]
        return AssistantReply(
            status="needs_clarification",
            message=question,
            clarification=Clarification(field=field, question=question),
        )
