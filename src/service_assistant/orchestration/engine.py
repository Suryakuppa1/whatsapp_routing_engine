from pydantic import ValidationError

from service_assistant.domain.contracts import AssistantReply, Clarification, ConversationState
from service_assistant.orchestration.contracts import (
    IntentInterpretation,
    IntentProvider,
    ProviderError,
)
from service_assistant.orchestration.policies import policy_reply
from service_assistant.services.discovery import ServiceDiscovery
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
        values = state.model_dump(exclude={"pending_field"})
        if "electricity" in message.casefold():
            if state.intent != "electricity":
                values = ConversationState(intent="electricity").model_dump(
                    exclude={"pending_field"}
                )
        elif state.pending_field:
            values[state.pending_field] = message[:200]
        else:
            values = ConversationState().model_dump(exclude={"pending_field"})
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
        if reply.clarification and reply.clarification.field != "service":
            state.pending_field = reply.clarification.field
        return state, reply

    @staticmethod
    def _clarify(field: str) -> AssistantReply:
        question = QUESTIONS[field]
        return AssistantReply(
            status="needs_clarification",
            message=question,
            clarification=Clarification(field=field, question=question),
        )
