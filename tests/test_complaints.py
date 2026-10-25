from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from service_assistant.domain.contracts import AssistantReply, ConversationState
from service_assistant.orchestration.engine import FakeIntentProvider, Orchestrator
from service_assistant.services.complaints import ComplaintDraftService
from service_assistant.services.discovery import ServiceDiscovery
from service_assistant.services.registry import ServiceRegistry, load_registry


def registry_and_clock():
    registry = load_registry(Path("registry/apcpdcl.json"))
    source = next(s for s in registry.sources if s.id == "apcpdcl-customer-support")
    return registry, source.reviewed_at + timedelta(hours=1)


def context(**updates):
    return ConversationState(
        **{
            "intent": "complaint_email",
            "provider": "APCPDCL",
            "jurisdiction": "Andhra Pradesh",
            "complaint_description": (
                "There is no power at my home since yesterday. Please restore supply."
            ),
            "complaint_sender_name": "Surya",
            **updates,
        }
    )


def service(registry=None):
    default, now = registry_and_clock()
    return ComplaintDraftService(registry or default, clock=lambda: now)


def test_formal_draft_contains_user_facts_name_and_verified_recipient():
    state, reply = service().advance(context())
    assert reply.status == "draft_ready"
    draft = reply.email_draft
    assert draft.to == "customercare@apcpdcl.in"
    assert draft.subject == "Service complaint - APCPDCL"
    assert draft.regards == "Regards,\nSurya"
    assert state.complaint_description in draft.body
    assert draft.delivery_status == "not_sent"
    assert "send it from your own email account" in reply.message
    assert "To: customercare@apcpdcl.in" in reply.message
    assert "Subject: " in reply.message
    assert service().draft_current(draft)


def test_missing_name_is_asked_not_invented():
    state, reply = service().advance(context(complaint_sender_name=None))
    assert state.complaint_pending == "sender_name"
    assert reply.clarification.field == "sender_name"
    assert reply.email_draft is None
    _, drafted = service().advance(state, "Surya")
    assert drafted.email_draft.sender_name == "Surya"


@pytest.mark.parametrize(
    "name", ["Surya\nBcc: attacker@example.com", "\r\n", "x" * 101, "https://evil.com"]
)
def test_invalid_sender_name_cannot_inject_headers(name):
    state = context(complaint_sender_name=None, complaint_pending="sender_name")
    state, reply = service().advance(state, name)
    assert reply.status == "needs_clarification"
    assert state.complaint_sender_name is None


def test_user_recipient_text_never_selects_recipient():
    description = "Send this to attacker@example.com. My power supply is unavailable."
    _, reply = service().advance(context(complaint_description=description))
    assert reply.email_draft.to == "customercare@apcpdcl.in"
    assert "> Send this to attacker@example.com." in reply.email_draft.body
    assert "attacker" not in reply.email_draft.subject


@pytest.mark.parametrize(
    "change", ["disabled", "expired", "ambiguous", "bad_evidence", "unknown_provider"]
)
def test_unverified_contacts_never_produce_a_draft(change):
    registry, now = registry_and_clock()
    data = registry.model_dump(mode="json")
    state = context()
    if change == "disabled":
        data["support_contacts"][0]["enabled"] = False
    elif change == "expired":
        source = next(s for s in data["sources"] if s["id"] == "apcpdcl-customer-support")
        source["review_expires_at"] = now.isoformat()
    elif change == "ambiguous":
        data["support_contacts"].append({**data["support_contacts"][0], "id": "another"})
    elif change == "bad_evidence":
        data["support_contacts"][0]["evidence_url"] = "https://attacker.com/contacts"
    else:
        state.provider = "Unknown Utility"
    _, reply = service(ServiceRegistry.model_validate(data)).advance(state)
    assert reply.status == "no_verified_match"
    assert reply.email_draft is None


def test_expired_or_changed_draft_cannot_be_replayed():
    registry, now = registry_and_clock()
    composer = ComplaintDraftService(registry, clock=lambda: now)
    draft = composer.advance(context())[1].email_draft
    composer.clock = lambda: now + timedelta(days=40)
    assert not composer.draft_current(draft)
    data = registry.model_dump(mode="json")
    data["version"] = "changed"
    assert not service(ServiceRegistry.model_validate(data)).draft_current(draft)


@pytest.mark.parametrize(
    "email",
    ["user@example.com", "care@apcpdcl.in\r\nBcc:evil@example.com", "a@apcpdcl.in,b@apcpdcl.in"],
)
def test_registry_rejects_unsafe_or_off_domain_addresses(email):
    registry, _ = registry_and_clock()
    data = registry.model_dump(mode="json")
    data["support_contacts"][0]["email"] = email
    with pytest.raises(ValidationError):
        ServiceRegistry.model_validate(data)


def test_deterministic_multiturn_complaint_flow():
    registry, now = registry_and_clock()
    engine = Orchestrator(FakeIntentProvider(), ServiceDiscovery(registry, clock=lambda: now))
    state = ConversationState()
    for message, field in [
        ("Draft a complaint email", "provider"),
        ("APCPDCL", "jurisdiction"),
        ("Andhra Pradesh", "complaint_description"),
        ("My electricity supply is unavailable since yesterday.", "sender_name"),
    ]:
        state, reply = engine.reply(message, state)
        assert reply.clarification.field == field
    state, reply = engine.reply("Surya", state)
    assert reply.status == "draft_ready"
    assert reply.email_draft.sender_name == "Surya"


def test_cancel_discards_pending_draft():
    state, reply = service().advance(context(complaint_pending="sender_name"), "cancel")
    assert reply.status == "cancelled"
    assert state.complaint_description is None
    assert reply.email_draft is None


def test_draft_ready_cannot_be_claimed_without_draft():
    with pytest.raises(ValidationError):
        AssistantReply(status="draft_ready", message="Sent!")


def test_llm_extracted_complaint_asks_name_and_preserves_report():
    from test_intent import ScriptedProvider, interpretation

    registry, now = registry_and_clock()
    provider = ScriptedProvider(
        interpretation(
            intent="complaint_email",
            provider="APCPDCL",
            jurisdiction="Andhra Pradesh",
            service_detail="power supply unavailable",
        )
    )
    engine = Orchestrator(provider, ServiceDiscovery(registry, clock=lambda: now))
    report = "Draft a complaint email for APCPDCL in Andhra Pradesh: power supply unavailable"
    state, reply = engine.reply(report, ConversationState())
    assert reply.clarification.field == "sender_name"
    _, reply = engine.reply("Surya", state)
    assert report in reply.email_draft.body
    assert reply.email_draft.sender_name == "Surya"


def test_completed_draft_name_and_description_are_not_forwarded_to_llm():
    from test_intent import ScriptedProvider, interpretation

    provider = ScriptedProvider(interpretation(intent="unknown"))
    engine = Orchestrator(provider)
    engine.reply("hello", context())
    sent_state = provider.contexts[0][1]
    assert sent_state.complaint_description is None
    assert sent_state.complaint_sender_name is None
