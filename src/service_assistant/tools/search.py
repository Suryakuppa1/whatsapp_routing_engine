from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class SearchQuery:
    hostname: str
    provider: str
    jurisdiction: str
    service: str

    def text(self) -> str:
        # Only reviewed registry metadata is used; never forward raw chat text.
        return f"site:{self.hostname} {self.provider} {self.jurisdiction} {self.service}"


class SearchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    url: str = Field(min_length=1, max_length=2048)


class SearchProvider(Protocol):
    def search(self, query: SearchQuery) -> tuple[SearchCandidate, ...]: ...


class SearchError(Exception):
    def __init__(self, code: str, retryable: bool):
        self.code, self.retryable = code, retryable
        super().__init__(code)
