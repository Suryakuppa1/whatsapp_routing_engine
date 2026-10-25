# Architecture and incremental roadmap

## Boundaries

`api` validates HTTP and authenticates callers. `services` coordinates transactions
and conversation lifecycle. `orchestration` interprets channel-independent domain
requests. `repositories` stores state. `infrastructure` owns configuration and
database construction. `channels` defines transport interfaces.

The core imports no transport SDK. FastAPI is confined to the API layer. Provider
replacement is constructor-injected through `IntentProvider`. Fake output must
never be presented as an actual model interpretation or verified discovery.

## Foundation schema

- `conversations`: UUID, authenticated owner, structured JSONB state, expiry, creation.
- `messages`: UUID, conversation foreign key, role, text, creation.
- `idempotency_records`: UUID, unique owner/key, request hash, structured response, expiry.

All changes for a turn commit atomically. PostgreSQL transaction advisory locks
serialize identical caller/key pairs; row locks serialize updates to a conversation.
The OpenAI call currently runs inside the transaction with bounded HTTP timeouts
and response-size limits. This preserves ordering and prevents duplicate paid
calls during concurrent retries, but holds a connection while waiting on the model.
This is an MVP tradeoff: introduce run leases or optimistic writes before high-load
deployment. HTTP phase timeouts are not a strict end-to-end wall-clock deadline.

## Milestones

1. Foundation and persisted deterministic chat — implemented.
2. Structured LLM interpretation and clarification — implemented with mock contracts;
   secure credentials, live smoke test, and real model behavior evaluation pending.
3. Reviewed local provider/source registry and explicit service plugins — implemented;
   production source review and population remain deployment work.
4. Offline official URL verification and constrained Brave Search fallback — implemented
   with mocked provider contracts. Live search smoke tests and network verification remain pending.
5. Telegram webhook, authenticated normalization, inbox deduplication and delivery outbox.
6. WhatsApp adapter contract tests, production hardening and operational documentation.

Future schema adds channel identities, assistant runs, tool executions, providers,
trusted sources, service entries, verification results, and inbox/outbox records.
Add these through migrations when their workflows are implemented rather than
creating unused tables now.

## Trust and action policy

An LLM interpretation is a proposal. Application policy controls capabilities,
required fields, and evidence. Search content is untrusted. Links must be supported
by registry-backed host/path/provider rules and pass independent relevance checks.
Reachability alone does not establish official status. Candidate fetching requires
SSRF protection, including DNS and redirect checks, plus bounded requests.

Response links come only from reviewed registry discovery. The assistant
does not execute payments, capture credentials, request OTPs, or submit forms.

The model's strict schema contains only intent, provider, jurisdiction, service
details, and action classification. Required fields/questions are deterministic.
Slot values must be grounded in the current message or same-intent previous field;
URLs and obvious secrets are rejected. The extractor receives structured context
and a versioned prompt. Provider exceptions contain only safe codes; failed turns
roll back without storing raw model output. No schema migration is required for
the added optional JSONB context fields; existing conversation records still load.

## Scaling

API replicas share PostgreSQL and retain no conversation state in memory. The
foundation uses sync SQLAlchemy in FastAPI's worker threads with bounded connection
pools. Later asynchronous inbox workers handle channel events and outbox delivery;
approved search/LLM clients need timeouts, backoff, budgets, and schema validation.
Implement a distributed admission/rate-limit mechanism before public exposure.

## Approved search boundary

A pinned registry URL bypasses search. A unique reviewed service without a pinned
URL can use explicit service-specific search paths that are also source-approved.
One bounded Brave request yields at most five URL-only candidates. Titles and
snippets never reach the LLM. Unregistered and ambiguous results return no links;
cached evidence is revalidated without another search. See [search](search.md).

## Assisted handoffs

Provider-specific `HandoffAdapter` implementations live outside orchestration and
channels. The APCPDCL adapter provides manual official-site instructions, never
form execution. The core persists a pending service ID/registry version and handles
explicit yes/no without a model call. Decline clears the pending flow; acceptance
revalidates discovery before returning a verified link and steps. Review expiry,
registry revision, and destination mismatch prevent stale handoffs. See
[APCPDCL](apcpdcl.md).
