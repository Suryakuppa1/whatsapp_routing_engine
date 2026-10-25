# User-sent complaint email drafts

The assistant finds a reviewed official support address, collects the user's
complaint and chosen name/username, and returns a formal draft. The user reviews
and sends it from their own email account. No email is sent, no mailbox is accessed,
and no complaint is represented as filed. There is no SMTP or email-send tool.

## Try the flow

Enable the APCPDCL preset with `SERVICE_REGISTRY_PATH=registry/apcpdcl.json`.
With the fake provider, send these messages to `/api/chat` using the same returned
conversation ID and a new idempotency key for each message:

1. `Please compose a complaint email`
2. `APCPDCL`
3. `Andhra Pradesh`
4. Describe the problem and the requested help.
5. Supply the name or username to use after `Regards`.

A live intent provider can classify `complaint_email` and extract provider,
location, and a short issue description from an initial request. The full initial
report is preserved when actual issue details are extracted. Missing fields are
asked one at a time. The name is always requested explicitly; channel display
names are not silently assumed. `cancel`, `stop`, or `no` during collection stops
and clears the pending complaint context without sending anything.

Recipients come only from reviewed `support_contacts`, never user text, LLM
output, or search snippets. Missing, ambiguous, disabled, expired, wrong-location,
or off-domain evidence yields `no_verified_match` rather than an invented address.

## API result

The new `draft_ready` outcome contains `email_draft` with:

- `to`, `subject`, `body`, `regards`, and `sender_name`;
- provider, contact/source IDs, official evidence URL, registry version, and review timestamps;
- `verification=registry_verified`, `delivery_status=not_sent`;
- `user_action=review_and_send_from_your_email`.

`message` contains a copyable plain-text version:

```text
To: customercare@apcpdcl.in
Subject: Service complaint - APCPDCL

Dear APCPDCL Customer Support Team,

I am writing to request assistance with the following issue:

> [User's own complaint details]

Location: Andhra Pradesh

Please investigate this matter, advise on the next steps, and provide a complaint
reference number and expected resolution timeline.

Thank you for your assistance.

Regards,
[Name explicitly supplied by the user]

This is a draft; nothing has been sent. Please review it, add any necessary service
references in your email app, and send it from your own email account.
```

The draft uses a deterministic formal template and quotes the user's report. It
does not invent dates, losses, service numbers, complaint IDs, or prior contact.
Header/newline injection in the sign-off name is rejected. Subject and recipient
are not taken from untrusted complaint text. The body and regards are separate
structured fields; a user should include both when copying into an email client.

## WhatsApp and Telegram boundary

The draft is channel-independent. `channels.rendering.render_plain_messages`
returns plain-text chunks with a default 3500 UTF-16-unit budget, including safe
handling of long lines and emoji. Future adapters should send those chunks to the
original authenticated conversation without enabling HTML/Markdown parsing.
Actual WhatsApp/Telegram webhook and delivery adapters are not implemented yet;
this increment provides the API flow and channel-ready content, not live bot delivery.

## Reviewed contact evidence

[APCPDCL's corporate directory](https://apcpdcl.in/contact-phno-corporate.php), linked
from [its contact page](https://apcpdcl.in/phone-numbers.php), lists
`customercare[at]apcpdcl[dot]in` under CCC. The preset records the single normalized
address `customercare@apcpdcl.in`, that exact official evidence page, review time,
and review expiry. This is evidence of publication, not proof of mailbox delivery
or a guaranteed support response time. No test message or SMTP probe was sent.

For another provider, add a reviewed source with the exact evidence hostname/path
and a `support_contacts` entry referencing it, jurisdiction aliases, and one plain
email address on that source's domain. Cross-domain published addresses require a
future explicit trust-policy extension; merely sharing a known provider name does
not make an arbitrary address official. Change the registry version on edits.

## Persistence and limits

Pending complaint details and the chosen name persist in the existing conversation
JSONB; the completed response persists in the idempotency record. Cached drafts
are rechecked against the current contact, address, evidence URL, review window,
and registry version before replay. Changed evidence returns `409 VERIFICATION_EXPIRED`.
Older conversations/responses load with the new optional fields unset; no migration
is needed. Completed draft names/reports are excluded from subsequent LLM context.

Complaint text and names are personal data stored in the application database.
They are excluded from structured operational logs. Existing conservative secret
guards still apply and are not a complete PII detector. Users add service references
in their own email client, and should never send credentials, OTPs, or payment
information to the assistant. A production retention/deletion policy is still needed.
