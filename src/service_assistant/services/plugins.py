from dataclasses import dataclass

from service_assistant.domain.contracts import ServiceIntent, SlotName


@dataclass(frozen=True)
class ServicePlugin:
    intent: ServiceIntent
    required_fields: tuple[SlotName, ...]


DEFAULT_PLUGINS = (
    ServicePlugin("electricity", ("provider",)),
    ServicePlugin("government_form", ("jurisdiction", "service_detail")),
    ServicePlugin("appointment", ("jurisdiction", "service_detail")),
    ServicePlugin("service_issue", ("provider", "service_detail")),
)


def plugin_map(plugins: tuple[ServicePlugin, ...] = DEFAULT_PLUGINS) -> dict[str, ServicePlugin]:
    result = {plugin.intent: plugin for plugin in plugins}
    if len(result) != len(plugins):
        raise ValueError("Duplicate service plugins")
    return result
