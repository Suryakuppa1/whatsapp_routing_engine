# APCPDCL assisted payment handoff

This integration returns instructions and the official BillDesk link after the
user confirms they want it. It does not prefill a form, look up a bill, calculate
an amount, create a checkout session, or initiate a payment.

## Enable

From the project root, set:

```dotenv
SERVICE_REGISTRY_PATH=registry/apcpdcl.json
```

The preset is opt-in; leaving the setting unset still loads an empty registry.
The Docker image includes this public reviewed preset. For production, a reviewed
registry can instead be mounted read-only with the same setting pointing at it.
The included review expires at `2026-11-24T00:00:00Z`; review the official links
again and update review metadata/version before that date. Expired evidence is
rejected, including during confirmation and cached-answer replay.

## Conversation

With the deterministic fake provider, send these messages to `/api/chat`, reusing
the returned conversation ID and using a new idempotency key for each turn:

1. `I need to pay my electricity bill`
2. `APCPDCL`
3. `Andhra Pradesh`
4. `yes` to receive the official link, or `no` to stop.

A supported live intent provider can extract the same context in fewer turns.
The confirmation is for receiving the link, not permission for the assistant to
pay. It is handled deterministically without an LLM call. `yes`, `y`, `continue`,
and `proceed` accept; `no`, `n`, `cancel`, and `stop` decline. Other replies ask for
an explicit yes/no. Source changes or expiry can prevent a previously offered
handoff. Registry version changes require starting a new service request.

Before confirmation the response has `status=needs_clarification`,
`clarification.field=handoff_confirmation`, `handoff.status=awaiting_confirmation`,
and no links. On acceptance it has `status=answered`, one verified link, and:

```json
{
  "service_id": "apcpdcl-electricity-bill",
  "mode": "manual_official_site",
  "status": "ready_for_user",
  "automatic_prefill": false,
  "payment_by": "user_on_official_site",
  "user_steps": [
    "Open the official APCPDCL BillDesk page.",
    "Enter your service number directly on that page.",
    "Complete the CAPTCHA yourself on the official page.",
    "Review the bill and amount there before choosing whether to pay.",
    "Enter payment details and authorize payment only on the official checkout."
  ]
}
```

Declining returns `status=cancelled`, `handoff.status=declined`, no links, and clears
the pending confirmation. Nothing is submitted or paid. Both decisions persist
through the existing conversation transaction and support idempotent replay.

The user enters their service number, CAPTCHA, credentials, payment details and
any payment authorization directly on the provider's pages. The assistant does
not request those fields. Unexpected confirmation text is withheld from message
storage so an accidentally pasted service number is not stored as text. Existing
conservative secret guards remain in place; they are not a complete PII detector
for arbitrary earlier conversation messages. Never put a service number in a URL
query string, return browser cookies, or claim this generic link resumes a
prefilled payment session.

## Reviewed public evidence

Public pages inspected on 2026-10-25:

- [APCPDCL homepage](https://apcpdcl.in/) links its “Bill Desk Payment” action to
  [the APCPDCL BillDesk page](https://payments.billdesk.com/MercOnline/CPDCLAPPGController).
- The BillDesk page displays service-number and CAPTCHA inputs.
- The homepage's “Know & Pay Your Bill” destination timed out during inspection;
  that endpoint is not used in this preset.

No customer information was entered and no payment flow was submitted. Public
inspection did not establish a supported prefill API or transferable checkout
session. No CAPTCHA is solved or bypassed. Bill retrieval, full checkout behavior,
redirects after submission, and payment status verification remain unverified.
Response evidence remains `reachability=not_checked`: the running application
does not make a fresh availability request for each handoff.

## Extension boundary

`HandoffAdapter` provides provider-specific instructions outside the core and
channels. A registry entry selects the adapter using `handoff_adapter`. Unknown
adapters fail startup; an adapter rejects an unexpected destination. The APCPDCL
adapter requires the exact reviewed BillDesk controller and APCPDCL provider.

New optional conversation fields persist the pending service ID and registry
version. `handoff` is an additive response field; older responses load with null.
No database migration or new dependency is required. Applications consuming the
API should render `handoff.user_steps` and understand the new `cancelled` outcome.
