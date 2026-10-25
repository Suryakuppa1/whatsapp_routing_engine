import json
import time

import httpx
from pydantic import SecretStr

from service_assistant.tools.search import SearchCandidate, SearchError, SearchQuery

MAX_SEARCH_BYTES = 131072
MAX_RESULTS = 5


class BraveSearchProvider:
    def __init__(
        self,
        api_key: SecretStr,
        timeout: float = 5,
        transport: httpx.BaseTransport | None = None,
    ):
        self._timeout = timeout
        self._client = httpx.Client(
            base_url="https://api.search.brave.com/res/v1/",
            headers={
                "X-Subscription-Token": api_key.get_secret_value(),
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def search(self, query: SearchQuery) -> tuple[SearchCandidate, ...]:
        started = time.monotonic()
        try:
            with self._client.stream(
                "GET",
                "web/search",
                params={
                    "q": query.text(),
                    "count": MAX_RESULTS,
                    "safesearch": "strict",
                    "text_decorations": "false",
                },
            ) as response:
                if response.status_code != 200:
                    retryable = response.status_code in (408, 429) or response.status_code >= 500
                    raise SearchError("SEARCH_UNAVAILABLE", retryable)
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    if len(data) + len(chunk) > MAX_SEARCH_BYTES:
                        raise SearchError("SEARCH_INVALID_OUTPUT", False)
                    if time.monotonic() - started > self._timeout:
                        raise SearchError("SEARCH_TIMEOUT", True)
                    data.extend(chunk)
            envelope = json.loads(data)
            web = envelope.get("web", {})
            results = web.get("results", [])
            if not isinstance(results, list):
                raise SearchError("SEARCH_INVALID_OUTPUT", False)
            # Search text is untrusted: discard all titles, snippets, and extra fields.
            return tuple(SearchCandidate(url=item["url"]) for item in results[:MAX_RESULTS])
        except httpx.TimeoutException:
            raise SearchError("SEARCH_TIMEOUT", True) from None
        except httpx.RequestError:
            raise SearchError("SEARCH_UNAVAILABLE", True) from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise SearchError("SEARCH_INVALID_OUTPUT", False) from None
