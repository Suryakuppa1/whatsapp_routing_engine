# Reviewed service registry

Increment 3 adds local registry discovery and offline URL verification. No source
is trusted by default. There are no seeded real-world providers: choosing the
initial jurisdiction and reviewing official sources remain deployment tasks.
Synthetic records exist only in tests and a disabled authoring example.

## Authoring and deployment

Copy `examples/registry.example.json` to a deployment-controlled file. Replace the
synthetic provider, hostname, exact paths, jurisdiction aliases, service URLs, and
review evidence with real reviewed values. Record who reviewed the authority and
where the evidence came from in `reviewed_by` and `provenance`. Set realistic
`reviewed_at` and `review_expires_at` timestamps including a timezone. Enable both
the source and service only after review, and increment `version` on every change.

Set `SERVICE_REGISTRY_PATH` to the JSON file's path and restart the API. A malformed,
oversized, or inconsistent file fails startup. No path or trust rule can come from
the LLM or an inbound chat. A file is loaded as an immutable process snapshot;
distribute the same version to every replica. Revocations require redeploy/restart
of all replicas. Expiration is checked on every discovery and cached-answer replay.

The registry is file-backed in this increment. Conversations and returned evidence
remain in PostgreSQL. A database-backed registry/editor and audit workflow can
replace the loader later without changing the channel or LLM contracts.

For Docker, mount the registry read-only at a container path and set
`SERVICE_REGISTRY_PATH` to that path through a Compose override. Mount it in the
API service; do not bake mutable trust policy or credentials into the image.

## Matching and service plugins

The application selects an explicitly registered `ServicePlugin` by intent.
Plugins declare required fields; the model only extracts context. Registry matching
uses exact normalized aliases (case and repeated whitespace are ignored), not fuzzy
or substring matching. Every supplied provider, location, and service detail must
match the entry. Use reviewed aliases for supported user vocabulary.

Electricity requests require a provider first. A matching registry service also
requires an explicit jurisdiction before a link is returned, even for a unique
provider. Forms/appointments require jurisdiction and service details; service
issues require provider and issue details. Ambiguous matches ask for another
missing discriminating field or return `no_verified_match`. The first candidate
is never silently selected. Empty, expired, disabled, conflicting, or unsafe
candidates yield no links. Web search fallback is not implemented yet.

## URL policy and evidence

Allowed URLs must use HTTPS with exactly the trusted ASCII hostname and one of its
exact approved paths. No wildcard/subdomain inheritance, userinfo, explicit ports,
query strings, fragments, encoded paths, dot traversal, controls, Unicode/punycode
hosts, IP literals, or local/internal host suffixes are accepted. This intentionally
small URL subset can exclude legitimate services; extend it only with appropriate
validation and tests. A `.gov` suffix alone never creates trust.

Verification is **offline registry verification**. The application does not resolve
DNS, fetch candidates, follow redirects, or claim the page is currently available.
Consequently there is no URL-fetch SSRF surface in this phase. This verifier must
not be reused as network authorization: future fetching needs DNS/IP checks,
connection pinning or equivalent rebinding protection, redirect verification,
response limits, and timeouts. An approved page's current redirect destination and
content have not been inspected by this implementation.

An `answered` response contains structured `links` with:

- `label`, `url`, `provider`, `service_id`, and `source_id`;
- `verification: "registry_verified"` and `registry_version`;
- `verified_at` (when policy was evaluated) and `source_reviewed_at`;
- `reachability: "not_checked"`.

Only answered responses can contain links, and answered responses require evidence.
The response text explicitly states that live availability was not checked. Model
output cannot supply links. Source/service IDs and registry version accompany
structured verification logs without message bodies or untrusted URLs.

The structured response, including evidence, is stored transactionally in the
idempotency record. Cached links are revalidated against the current registry,
review window, approved URL, provider, label, and version before replay. Changes
return `409 VERIFICATION_EXPIRED` without the old links; retry with a new idempotency
key. A version change conservatively invalidates all cached link answers.

No database migration is needed for this phase: evidence fits the existing JSONB
response column, and older records with empty `links` continue to deserialize.
