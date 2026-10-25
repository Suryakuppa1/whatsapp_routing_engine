# WhatsApp Routing Engine

Channel-independent AI Service Assistant backend.

Channel-independent FastAPI backend. **Increment 3: reviewed registry discovery.**
Includes a deterministic fake and an OpenAI Responses API adapter. Live-provider
verification is pending secure credential setup. Official links can be returned
from a deployment-controlled reviewed registry; web search is not available yet.

## Implemented

- Authenticated `POST /api/chat` with validated request/response schemas.
- PostgreSQL conversations, messages, and transactional idempotency records.
- Electricity-provider clarification across turns and process restarts.
- Per-client conversation ownership and sliding session expiration.
- Cross-process PostgreSQL locks for duplicate requests and conversation updates.
- Sanitized JSON errors and structured request logs without message bodies or tokens.
- Conservative guards for obvious secrets and requests to carry out actions.
- Alembic migration, Docker configuration, unit and real PostgreSQL integration tests.
- Transport-neutral channel contract; no concrete channel adapters yet.
- Schema-validated interpretation for electricity, forms, appointments, and service issues.
- Application-controlled clarification, slot provenance checks, and safe upstream errors.
- OpenAI adapter with request timeouts, response-size limits, no automatic retries,
  no redirects, and provider-side response storage disabled (`store=false`).
- Explicit service plugins, reviewed registry loading, exact service matching,
  offline URL verification, source expiry/revocation checks, and structured evidence.

The default registry is empty. To enable real services, follow the
[registry authoring and verification guide](docs/registry.md). No synthetic test
record is enabled in the application.

## Local setup

Python 3.12+ and PostgreSQL 17 are recommended. From this directory:

```bash
uv sync --extra dev --locked
cp .env.example .env.local
```

Edit `.env.local` to set your PostgreSQL URL and replace the example application
token with a random token of at least 32 characters. Each named API client owns a
separate set of conversations. Do not use one shared client token as end-user
authentication in a public application.

```bash
uv run alembic upgrade head
uv run uvicorn service_assistant.main:app --reload --no-access-log
```

Configuration loads `.env.local` relative to the working directory. Environment
variables override file settings. Startup fails if tokens are missing, duplicated,
too short, or still use the example placeholder. The OpenAI key was **not created**:
the secure save helper is unavailable in this execution environment. The fake
provider requires no key. `LLM_PROVIDER` accepts `fake` (default) or `openai`.
The latter requires `OPENAI_API_KEY` supplied securely. `OPENAI_MODEL` defaults to
`gpt-4.1-mini`; choose a model that supports Responses API strict structured outputs.
`LLM_TIMEOUT_SECONDS` defaults to 8 and bounds HTTP phases and streamed reads.
No real OpenAI requests have been made or verified in this environment. When
enabled, the provider receives the current allowed message, structured prior slots,
and locale, not the complete conversation transcript. `store=false` does not
replace the provider's account-level data retention policy.

## API

`GET /health/live` checks the process. `GET /health/ready` checks database access
and the migrated foundation tables. OpenAPI is available at `/docs`.

`POST /api/chat` requires `Authorization: Bearer <application-token>` and a unique
`Idempotency-Key` header (1–128 ASCII letters, digits, periods, underscores, colons,
or hyphens).

```json
{"message":"I need to pay my electricity bill","locale":"en-IN"}
```

The response includes a `conversation_id`, `request_id`, `status`, `message`,
`clarification`, and `links`. Continue by sending the conversation ID and a
new idempotency key:

```json
{"conversation_id":"<returned UUID>","message":"Example Utility"}
```

The fake provider asks for the electricity provider, then returns
`no_verified_match`. Other requests receive a generic clarification; obvious
sensitive/action requests receive `unsupported`. Only English locales are
supported in this increment. With a matching reviewed registry, it asks for the
jurisdiction and can return `answered` with a structured verified link. Evidence is
marked `registry_verified`, and live reachability is explicitly `not_checked`.

With OpenAI enabled, forms and appointments ask for jurisdiction and service
details, while service issues ask for the provider and issue. The model extracts
complete context; application code selects required fields and fixed questions.
New slot values must appear in the current message, or match the same field of
the previous context for that intent. This conservative check can reject valid
paraphrases; it prevents accepting invented provider/location details. Model text
never becomes a user-facing question, link, or instruction. Registry entries supply
the only permitted response links.

Upstream timeouts/rate limits/transient failures return sanitized `503` errors.
Malformed, refused, ungrounded, or nonretryable upstream responses return `502`.
Failed turns roll back; callers can safely retry with the same idempotency key.

Replaying an identical request with the same client/key returns the stored response
for 24 hours. Reusing that key for different content returns `409`. The stored
response keeps its original `request_id`; the `X-Request-ID` header identifies the
current HTTP attempt. Sessions expire after 24 hours of inactivity by default;
expired sessions return `410`. A different client's conversation returns `404`.
Cached link answers are rechecked against current registry policy. Changed or
expired evidence returns `409 VERIFICATION_EXPIRED`; use a new idempotency key.

## Docker

Set `POSTGRES_PASSWORD` in `.env.local` to a random URL-safe password; Compose uses
it in the database URL. Keep `API_CLIENTS` configured as above.

```bash
docker compose --env-file .env.local up --build
```

Compose starts PostgreSQL, runs migrations once, then starts the API bound to
localhost. The database has no host port. Secrets are excluded from the image.
Docker could not be executed in this workspace because daemon access is blocked;
the image and Compose configuration have not been runtime-tested here.

For managed environments whose HTTPS proxy needs a custom CA, build with the
system CA bundle as a BuildKit secret, retaining the environment's proxy settings:

```bash
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -t service-assistant .
```

Use that image via a local Compose override if needed. Do not disable TLS
verification. Standard Docker builds do not require the optional secret.

## Tests

```bash
uv run pytest -q
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
```

Integration tests skip unless `TEST_DATABASE_URL` points to a **dedicated disposable
PostgreSQL database**. They migrate, downgrade/re-upgrade, compare schema metadata,
and truncate application tables. Never point them at production or valuable data.

```bash
TEST_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/assistant_test uv run pytest -q
```

Tests cover clarification, restart persistence, ownership, expiration, concurrent
duplicates, idempotency conflicts, transaction rollback, sensitive input handling,
and sanitized dependency failures. Provider contract tests use HTTPX mock transport
for valid outputs, malformed JSON, refusal, incomplete output, unexpected tools,
rate limits, authentication failures, timeouts, and oversized responses. No paid
API calls are made. These tests validate integration behavior, not real model
intent accuracy; behavioral evaluation with a live model is still pending.

## Limits and next increment

This is a development increment, not a production deployment. It has no verified
live LLM connection, network URL fetching, search integration, Telegram bot, delivery worker, or
WhatsApp implementation. The channel contract is the future integration boundary.

Normal conversation text is persisted. The conservative secret guard withholds
obvious sensitive messages from text/state storage but is not a complete PII
detector, and numeric guards may reject legitimate reference numbers. Session
expiry does not delete records. Production rollout requires a retention/deletion
policy, rate limits, request-size limits, deployment TLS, and end-user identity
mapping. Keep PostgreSQL private; use TLS for remote database connections.

Next: an approved search-provider integration with safe unverified-result handling,
then Telegram webhook and delivery support.
Secure credential setup and a live structured-output smoke test remain pending.
See [the architecture and roadmap](docs/architecture.md).
