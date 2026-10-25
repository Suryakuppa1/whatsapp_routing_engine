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
from service_assistant.services.registry import (
    ServiceEntry,
    ServiceRegistry,
    TrustedSource,
    normalize,
)
from service_assistant.tools.search import SearchProvider, SearchQuery
from service_assistant.tools.verify_url import verify_url

logger = logging.getLogger("service_assistant.discovery")


class ServiceDiscovery:
    def __init__(
        self,
        registry: ServiceRegistry,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        search: SearchProvider | None = None,
    ):
        self.registry, self.clock, self.search = registry, clock, search
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
            if service.official_url and verify_url(
                service.official_url, source.hostname, source.allowed_paths
            ):
                self._log("registry_candidate_rejected", service, source)
                continue
            candidates.append((service, source))
        if not candidates:
            return self._no_match()
        # Provider names alone never imply a jurisdiction. Resolve ambiguity before network work.
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
        if len(candidates) != 1:
            return self._no_match()
        service, source = candidates[0]
        url = service.official_url
        method = "registry"
        if url is None:
            if self.search is None:
                return self._no_match()
            query = SearchQuery(
                hostname=source.hostname,
                provider=source.provider,
                jurisdiction=service.jurisdiction_aliases[0],
                service=service.label,
            )
            # One bounded provider call, at most five results; no recursion or scraping.
            results = self.search.search(query)
            urls = {
                candidate.url
                for candidate in results[:5]
                if not verify_url(candidate.url, source.hostname, service.search_paths)
            }
            self._log("search_candidates_evaluated", service, source)
            # Search ranking is not evidence for choosing between multiple approved pages.
            if len(urls) != 1:
                return self._no_match()
            url = next(iter(urls))
            method = "approved_search"
        # Recheck the review window after a potentially slow upstream request.
        now = self.clock()
        if not source.active(now):
            return self._no_match()
        self._log("registry_match_verified", service, source)
        return AssistantReply(
            status="answered",
            message=(
                "Here is the registered official service page. Live availability was not checked."
            ),
            links=[
                VerifiedLink(
                    label=service.label,
                    url=url,
                    provider=source.provider,
                    service_id=service.id,
                    source_id=source.id,
                    verified_at=now,
                    source_reviewed_at=source.reviewed_at,
                    registry_version=self.registry.version,
                    discovery_method=method,
                )
            ],
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
                or link.provider != source.provider
                or link.label != service.label
                or link.source_reviewed_at != source.reviewed_at
                or link.registry_version != self.registry.version
            ):
                return False
            if service.official_url is not None:
                if link.url != service.official_url or link.discovery_method != "registry":
                    return False
                paths = source.allowed_paths
            else:
                if link.discovery_method != "approved_search":
                    return False
                paths = service.search_paths
            if verify_url(link.url, source.hostname, paths):
                return False
        return True

    def _log(self, event: str, service: ServiceEntry, source: TrustedSource) -> None:
        logger.info(
            json.dumps(
                {
                    "event": event,
                    "service_id": service.id,
                    "source_id": source.id,
                    "registry_version": self.registry.version,
                }
            )
        )

    @staticmethod
    def _no_match() -> AssistantReply:
        return AssistantReply(
            status="no_verified_match",
            message=(
                "I couldn’t find a unique verified official service for those details. "
                "Please check the provider and location."
            ),
        )
