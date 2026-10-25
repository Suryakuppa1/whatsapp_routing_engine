from service_assistant.infrastructure.config import Settings
from service_assistant.infrastructure.search.brave import BraveSearchProvider
from service_assistant.tools.search import SearchProvider


def build_search_provider(settings: Settings) -> SearchProvider | None:
    if settings.search_provider == "disabled":
        return None
    assert settings.brave_search_api_key is not None
    return BraveSearchProvider(settings.brave_search_api_key, settings.search_timeout_seconds)
