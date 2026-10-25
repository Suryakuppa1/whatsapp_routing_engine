# Approved search integration

Increment 4 adds a replaceable `SearchProvider` with a Brave Web Search API adapter.
Search is disabled by default. No live Brave request has been made in this workspace;
contract tests use HTTPX mock transport. Live validation requires an authorized
Brave subscription, securely configured credentials, and network access to
`api.search.brave.com`.

## Configuration

Set `SEARCH_PROVIDER=brave` and supply `BRAVE_SEARCH_API_KEY` through your secret
manager or ignored environment configuration. Never submit this key through chat
or the application API. Startup rejects enabled search without a key. The key is
represented as `SecretStr` and is not included in logs or errors.

`SEARCH_TIMEOUT_SECONDS` defaults to 5 (range 1–8). The adapter uses a fixed HTTPS
endpoint, TLS verification, phase timeouts, a streamed body limit of 128 KiB, no
redirects, no automatic retries, and a maximum of five candidates. Stream timing
checks complement HTTP phase timeouts; this is not a strict total request deadline.

## Registry-first discovery

A reviewed pinned `official_url` is returned directly without a search request.
For a reviewed service catalog with no pinned URL, use:

```json
{
  "id": "reviewed-service-id",
  "source_id": "reviewed-provider-id",
  "intent": "electricity",
  "label": "Reviewed bill information",
  "jurisdiction_aliases": ["Reviewed jurisdiction"],
  "official_url": null,
  "search_paths": ["/approved-bill-information"],
  "enabled": true
}
```

The referenced source must explicitly list every `search_paths` entry in its
`allowed_paths` and have current review evidence. These are exact service-specific
paths, not arbitrary prefixes. Review approves possible destinations; search finds
an indexed result among them rather than inventing a URL or relying on ranking.
The normal alias, provider, jurisdiction, intent, enabled, and review checks still
apply. Missing context is clarified before search; ambiguous service definitions
are not resolved by ranking.

Search never runs against an empty registry, unknown service, inactive source, or
unresolved catalog match. This is deliberately a constrained lookup, not open-web
research. A provider-domain match alone cannot establish service relevance. Wider
search needs a separate evidence/review workflow before unknown URLs can be returned.

## Untrusted results

The query is built only from the selected source's hostname/provider and reviewed
service label/jurisdiction. Raw user messages and conversation transcripts are not
sent to Brave. Search titles, snippets, and extra text are discarded before they
reach the orchestration layer; they cannot instruct the LLM, create trust rules,
or become user-facing instructions.

Every candidate must pass the existing exact HTTPS hostname/path verifier against
the matched service's `search_paths`. Other pages on the same official host are
not automatically valid. Duplicate identical URLs are deduplicated. Exactly one
approved URL is required; zero or multiple distinct approved results produce
`no_verified_match` with no links. The review window is checked again after search.

Evidence retains `verification=registry_verified`, adds
`discovery_method=approved_search`, and keeps `reachability=not_checked`. An indexed
result is not proof of current page availability or redirect behavior. No candidate
is fetched, no redirects are followed, and there is no URL-fetch SSRF surface.

## Failures and persistence

Timeouts, rate limits, and transient upstream failures return sanitized `503`
errors (`SEARCH_TIMEOUT` or `SEARCH_UNAVAILABLE`). Malformed output, oversized
bodies, authentication failures, and other nonretryable responses return `502`.
Raw upstream bodies and credentials are never returned. Failed turns roll back
and callers can retry using the same idempotency key.

Successful evidence is persisted in the existing JSONB response record. Cache
replay validates service-specific paths and source review again and never reruns
search. Registry revisions, revocation, and path removal invalidate cached links.
Existing pinned-URL responses load with `discovery_method=registry` by default.
No database migration is needed.

Search calls currently occur inside the conversation transaction, like intent
interpretation. Both have bounded network phases, but this still occupies a worker
and database connection; run leases or an asynchronous inbox are needed before
high-load deployment. Provider quotas and distributed rate limits also remain
production-hardening work.
