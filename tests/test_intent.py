import pytest
from pydantic import ValidationError

from service_assistant.domain.contracts import ConversationState
from service_assistant.orchestration.contracts import IntentInterpretation, ProviderError
from service_assistant.orchestration.engine import Orchestrator


def interpretation(**updates):
    return IntentInterpretation(
        **{
            "intent": "electricity",
            "provider": None,
            "jurisdiction": None,
            "service_detail": None,
            "action": "information",
            **updates,
        }
    )


class ScriptedProvider:
    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.contexts = []

    def interpret(self, message, state, locale="en-IN"):
        self.contexts.append((message, state.model_copy(deep=True), locale))
        return next(self.outputs)


def test_form_clarification_and_context():
    provider = ScriptedProvider(
        interpretation(intent="government_form"),
        interpretation(intent="government_form", jurisdiction="India"),
        interpretation(intent="government_form", jurisdiction="India", service_detail="passport"),
    )
    engine = Orchestrator(provider)
    state, reply = engine.reply("I need a government form", ConversationState())
    assert reply.clarification.field == "jurisdiction"
    state, reply = engine.reply("India", state)
    assert reply.clarification.field == "service_detail"
    state, reply = engine.reply("passport", state, "en-GB")
    assert reply.status == "no_verified_match"
    assert state.pending_field is None
    assert provider.contexts[-1][1].jurisdiction == "India"
    assert provider.contexts[-1][2] == "en-GB"


@pytest.mark.parametrize(
    "intent,field",
    [
        ("electricity", "provider"),
        ("appointment", "jurisdiction"),
        ("government_form", "jurisdiction"),
        ("service_issue", "provider"),
        ("unknown", "service"),
    ],
)
def test_required_fields_are_application_controlled(intent, field):
    engine = Orchestrator(ScriptedProvider(interpretation(intent=intent)))
    _, reply = engine.reply("Help", ConversationState())
    assert reply.clarification.field == field
    assert not reply.links


def test_topic_switch_clears_previous_slots():
    old = ConversationState(intent="electricity", provider="Old Utility")
    engine = Orchestrator(ScriptedProvider(interpretation(intent="appointment")))
    state, reply = engine.reply("Find a doctor appointment", old)
    assert state.provider is None
    assert state.intent == "appointment"
    assert reply.clarification.field == "jurisdiction"


def test_correction_replaces_provider():
    engine = Orchestrator(ScriptedProvider(interpretation(provider="New Utility")))
    state, _ = engine.reply(
        "Actually New Utility", ConversationState(intent="electricity", provider="Old Utility")
    )
    assert state.provider == "New Utility"


@pytest.mark.parametrize(
    "action,status",
    [
        ("execute", "unsupported"),
        ("unknown", "needs_clarification"),
    ],
)
def test_actions_do_not_execute(action, status):
    engine = Orchestrator(ScriptedProvider(interpretation(action=action)))
    _, reply = engine.reply("help", ConversationState())
    assert reply.status == status
    assert not reply.links


@pytest.mark.parametrize(
    "value", ["https://attacker.example", "www.attacker.example", "OTP 123456"]
)
def test_dangerous_slot_values_rejected(value):
    engine = Orchestrator(ScriptedProvider(interpretation(provider=value)))
    with pytest.raises(ProviderError, match="PROVIDER_INVALID_OUTPUT"):
        engine.reply(value, ConversationState())


def test_invented_provider_rejected():
    engine = Orchestrator(ScriptedProvider(interpretation(provider="Invented Utility")))
    with pytest.raises(ProviderError, match="PROVIDER_UNGROUNDED_OUTPUT"):
        engine.reply("I live in Mumbai", ConversationState())


@pytest.mark.parametrize(
    "updates",
    [
        {"links": ["https://attacker.example"]},
        {"provider": " "},
        {"intent": "payment_tool"},
        {"provider": "x" * 201},
        {"provider": 12},
    ],
)
def test_invalid_schema(updates):
    with pytest.raises(ValidationError):
        interpretation(**updates)
