from service_assistant.infrastructure.config import Settings
from service_assistant.infrastructure.llm.openai import OpenAIIntentProvider
from service_assistant.orchestration.contracts import IntentProvider
from service_assistant.orchestration.engine import FakeIntentProvider


def build_intent_provider(settings: Settings) -> IntentProvider:
    if settings.llm_provider == "fake":
        return FakeIntentProvider()
    assert settings.openai_api_key is not None  # Settings validates provider requirements.
    return OpenAIIntentProvider(
        settings.openai_api_key, settings.openai_model, settings.llm_timeout_seconds
    )
