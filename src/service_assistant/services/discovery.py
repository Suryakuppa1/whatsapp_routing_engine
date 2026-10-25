import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from service_assistant.domain.contracts import (
    AssistantReply,
    Clarification,
    ConversationState,
    VerifiedLink,
)
from service_assistant.services.registry import ServiceRegistry, normalize
from service_assistant.tools.verify_url import verify_url

logger = logging.getLogger("service_assistant.discovery")


class ServiceDiscovery:
    def __init__(
        self, registry: ServiceRegistry, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ):
        self.registry, self.clock = registry, clock
        self.sources = {source.id: source for source in registry.sources}

    def discover(self, state: ConversationState) -> AssistantReply:
        candidates = []
        now = self.clock()
        for service in self.registry.services:
            source = self.sources[service.source_id]
            if not service.enabled or not source.active(now) or service.intent != state.intent:
                continue
            if state.provider and normalize(state.provider) not in {
                normalize(alias) for alias in (source.provider, *source.provider_aliases)
            }:
                continue
            if state.jurisdiction and normalize(state.jurisdiction) not in {
                normalize(alias) for alias in service.jurisdiction_aliases
            }:
                continue
            if state.service_detail and normalize(state.service_detail) not in {
                normalize(alias) for alias in service.detail_aliases
            }:
                continue
            reason = verify_url(service.official_url, source.hostname, source.allowed_paths)
            if reason:
                logger.info(
                    json.dumps(
                        {
                            "event": "registry_candidate_rejected",
                            "service_id": service.id,
                            "reason": reason,
                            "registry_version": self.registry.version,
                        }
                    )
                )
                continue
            candidates.append((service, source))
        if candidates:
            # A provider name alone never selects a jurisdiction implicitly.
            field = "jurisdiction" if not state.jurisdiction else None
            if field is None and len(candidates) > 1:
                if not state.provider:
                    field = "provider"
                elif not state.service_detail:
                    field = "service_detail"
            if field:
                question = {
                    "jurisdiction": "Which country and state or city is this service for?",
                    "provider": "Which provider or organization is this for?",
                    "service_detail": "Which specific service do you need?",
                }[field]
                return AssistantReply(
                    status="needs_clarification",
                    message=question,
                    clarification=Clarification(field=field, question=question),
                )
            if len(candidates) == 1:
                service, source = candidates[0]
                logger.info(
                    json.dumps(
                        {
                            "event": "registry_match_verified",
                            "service_id": service.id,
                            "source_id": source.id,
                            "registry_version": self.registry.version,
                        }
                    )
                )
                return AssistantReply(
                    status="answered",
                    message=(
                        "Here is the registered official service page. "
                        "Live availability was not checked."
                    ),
                    links=[
                        VerifiedLink(
                            label=service.label,
                            url=service.official_url,
                            provider=source.provider,
                            service_id=service.id,
                            source_id=source.id,
                            verified_at=now,
                            source_reviewed_at=source.reviewed_at,
                            registry_version=self.registry.version,
                        )
                    ],
                )
        return AssistantReply(
            status="no_verified_match",
            message=(
                "I couldn’t find a unique verified official service for those details. "
                "Please check the provider and location."
            ),
        )

    def links_current(self, links: list[VerifiedLink]) -> bool:
        now = self.clock()
        for link in links:
            service = next((s for s in self.registry.services if s.id == link.service_id), None)
            source = self.sources.get(link.source_id)
            if (
                not service
                or not source
                or not service.enabled
                or not source.active(now)
                or service.source_id != source.id
                or service.official_url != link.url
                or link.provider != source.provider
                or link.label != service.label
                or link.source_reviewed_at != source.reviewed_at
                or link.registry_version != self.registry.version
                or verify_url(link.url, source.hostname, source.allowed_paths)
            ):
                return False
        return True
