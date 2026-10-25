from datetime import UTC, datetime
from pathlib import Path

import pytest

from service_assistant.domain.contracts import ConversationState
from service_assistant.orchestration.engine import FakeIntentProvider, Orchestrator
from service_assistant.services.discovery import ServiceDiscovery
from service_assistant.services.handoffs import APCPDCL_PAYMENT_URL
from service_assistant.services.registry import ServiceRegistry, load_registry

REVIEW_TIME = datetime(2026, 10, 25, 12, tzinfo=UTC)


def engine(registry=None, clock=lambda: REVIEW_TIME):
    registry = registry or load_registry(Path("registry/apcpdcl.json"))
    return Orchestrator(FakeIntentProvider(), ServiceDiscovery(registry, clock=clock))


def ready_context():
    return ConversationState(intent="electricity", provider="APCPDCL", pending_field="jurisdiction")


def test_confirmation_then_user_payment_handoff():
    core = engine()
    state, awaiting = core.reply("Andhra Pradesh", ready_context())
    assert awaiting.clarification.field == "handoff_confirmation"
    assert awaiting.handoff.status == "awaiting_confirmation"
    assert awaiting.links == []
    state, reply = core.reply("yes", state)
    assert reply.status == "answered"
    assert reply.links[0].url == APCPDCL_PAYMENT_URL
    assert reply.handoff.automatic_prefill is False
    assert reply.handoff.payment_by == "user_on_official_site"
    assert "CAPTCHA" in " ".join(reply.handoff.user_steps)
    assert "Nothing has been prefilled" in reply.message
    assert state.pending_handoff_service_id is None


@pytest.mark.parametrize("answer", ["no", "No", "cancel", "stop"])
def test_decline_stops_without_link(answer):
    core = engine()
    state, _ = core.reply("Andhra Pradesh", ready_context())
    state, reply = core.reply(answer, state)
    assert reply.status == "cancelled"
    assert reply.links == []
    assert reply.handoff.status == "declined"
    assert state.pending_handoff_service_id is None


def test_ambiguous_consent_does_not_release_link():
    core = engine()
    state, _ = core.reply("Andhra Pradesh", ready_context())
    state, reply = core.reply("maybe", state)
    assert reply.status == "needs_clarification"
    assert not reply.links
    assert state.pending_handoff_service_id


def test_consent_skips_llm_and_network():
    core = engine()
    state, _ = core.reply("Andhra Pradesh", ready_context())

    class FailingProvider:
        def interpret(self, *args):
            raise AssertionError("No model call during confirmation")

    core.provider = FailingProvider()
    assert core.reply("yes", state)[1].status == "answered"


def test_review_expiry_during_confirmation_blocks_handoff():
    core = engine()
    state, _ = core.reply("Andhra Pradesh", ready_context())
    core.discovery.clock = lambda: datetime(2026, 11, 25, tzinfo=UTC)
    assert core.reply("yes", state)[1].links == []


def test_registry_revision_requires_new_request():
    core = engine()
    state, _ = core.reply("Andhra Pradesh", ready_context())
    data = core.discovery.registry.model_dump(mode="json")
    data["version"] = "changed-policy"
    changed = engine(ServiceRegistry.model_validate(data))
    assert changed.reply("yes", state)[1].status == "no_verified_match"


def test_wrong_destination_cannot_use_apcpdcl_instructions():
    data = load_registry(Path("registry/apcpdcl.json")).model_dump(mode="json")
    data["sources"][0]["allowed_paths"] = ["/another-biller"]
    data["services"][0]["official_url"] = "https://payments.billdesk.com/another-biller"
    reply = engine(ServiceRegistry.model_validate(data)).reply("Andhra Pradesh", ready_context())[1]
    assert reply.status == "no_verified_match"
    assert reply.handoff is None


def test_unknown_adapter_rejected_at_startup():
    data = load_registry(Path("registry/apcpdcl.json")).model_dump(mode="json")
    data["services"][0]["handoff_adapter"] = "unknown"
    with pytest.raises(ValueError, match="unknown handoff adapter"):
        engine(ServiceRegistry.model_validate(data))


def test_revoked_source_cannot_be_confirmed():
    core = engine()
    state, _ = core.reply("Andhra Pradesh", ready_context())
    data = core.discovery.registry.model_dump(mode="json")
    data["sources"][0]["enabled"] = False
    revoked = engine(ServiceRegistry.model_validate(data))
    assert revoked.reply("yes", state)[1].links == []
