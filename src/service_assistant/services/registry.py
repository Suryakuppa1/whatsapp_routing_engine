import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from service_assistant.domain.contracts import ServiceIntent
from service_assistant.tools.verify_url import public_hostname, safe_path

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


class RegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TrustedSource(RegistryModel):
    id: Name
    provider: Name
    provider_aliases: tuple[Name, ...] = ()
    hostname: Name
    allowed_paths: tuple[Name, ...] = Field(min_length=1)
    provenance: Name
    reviewed_by: Name
    reviewed_at: AwareDatetime
    review_expires_at: AwareDatetime
    enabled: bool = True

    @model_validator(mode="after")
    def validate_policy(self):
        if not public_hostname(self.hostname) or not all(safe_path(p) for p in self.allowed_paths):
            raise ValueError(
                "Trust rules require a public ASCII hostname and exact canonical paths"
            )
        if self.review_expires_at <= self.reviewed_at:
            raise ValueError("Review expiry must follow review time")
        return self

    def active(self, now: datetime) -> bool:
        return self.enabled and self.reviewed_at <= now < self.review_expires_at


class ServiceEntry(RegistryModel):
    id: Name
    source_id: Name
    intent: ServiceIntent
    label: Name
    jurisdiction_aliases: tuple[Name, ...] = Field(min_length=1)
    detail_aliases: tuple[Name, ...] = ()
    official_url: str | None = Field(default=None, min_length=1, max_length=2048)
    search_paths: tuple[Name, ...] = ()
    handoff_adapter: Name | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def require_details(self):
        if self.official_url is None and not self.search_paths:
            raise ValueError("A service needs an official URL or explicit search paths")
        if not all(safe_path(path) for path in self.search_paths):
            raise ValueError("Search paths must be exact canonical paths")
        if self.intent == "unknown":
            raise ValueError("Unknown is not a registrable service")
        if self.intent != "electricity" and not self.detail_aliases:
            raise ValueError("This intent requires service-detail aliases")
        return self


class SupportContact(RegistryModel):
    id: Name
    source_id: Name
    jurisdiction_aliases: tuple[Name, ...] = Field(min_length=1)
    email: str = Field(min_length=3, max_length=254)
    evidence_url: str = Field(min_length=1, max_length=2048)
    purpose: str = Field(default="customer_complaints", pattern="^customer_complaints$")
    enabled: bool = True

    @model_validator(mode="after")
    def single_recipient(self):
        if not re.fullmatch(r"[A-Za-z0-9._+-]+@[a-z0-9.-]+\.[a-z]{2,63}", self.email):
            raise ValueError("Support contact must be a single plain email address")
        return self


class ServiceRegistry(RegistryModel):
    version: Name
    sources: tuple[TrustedSource, ...] = ()
    services: tuple[ServiceEntry, ...] = ()
    support_contacts: tuple[SupportContact, ...] = ()

    @model_validator(mode="after")
    def validate_references(self):
        ids = {source.id for source in self.sources}
        if len(ids) != len(self.sources) or len({s.id for s in self.services}) != len(
            self.services
        ):
            raise ValueError("Registry IDs must be unique")
        if any(service.source_id not in ids for service in self.services):
            raise ValueError("Every service must reference an existing trusted source")
        sources = {source.id: source for source in self.sources}
        if len({c.id for c in self.support_contacts}) != len(self.support_contacts):
            raise ValueError("Support contact IDs must be unique")
        for contact in self.support_contacts:
            source = sources.get(contact.source_id)
            if source is None:
                raise ValueError("Support contact needs an existing source")
            if contact.email.split("@", 1)[1] != source.hostname:
                raise ValueError("Contact email must belong to its reviewed source domain")
        if any(
            not set(service.search_paths).issubset(sources[service.source_id].allowed_paths)
            for service in self.services
        ):
            raise ValueError("Service search paths must be explicitly allowed by their source")
        return self


def load_registry(path: Path | None) -> ServiceRegistry:
    if path is None:
        return ServiceRegistry(version="empty-v1")
    # A deployment-controlled file, never a path supplied by a channel or model.
    if path.stat().st_size > 1_000_000:
        raise ValueError("Registry exceeds size limit")
    return ServiceRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))
