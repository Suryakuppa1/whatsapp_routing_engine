import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from service_assistant.api.app import create_app
from service_assistant.domain.contracts import ChatRequest, ConversationState
from service_assistant.infrastructure.config import Settings
from service_assistant.orchestration.engine import FakeIntentProvider, Orchestrator
from service_assistant.orchestration.policies import policy_reply


def test_clarification_and_followup():
    engine = Orchestrator(FakeIntentProvider())
    state, reply = engine.reply("Pay my electricity bill", ConversationState())
    assert reply.clarification.field == "provider"
    state, reply = engine.reply("Example Utility", state)
    assert state.provider == "Example Utility"
    assert reply.status == "no_verified_match"
    assert not reply.links


@pytest.mark.parametrize("message", ["my OTP is 123456", "password: secret", "pay it for me"])
def test_sensitive_or_action_requests_are_blocked(message):
    assert policy_reply(message).status == "unsupported"


def test_information_request_is_allowed():
    assert policy_reply("How do I pay my electricity bill?") is None


@pytest.mark.parametrize("message", ["", "   ", "a" * 4001])
def test_invalid_messages(message):
    with pytest.raises(ValidationError):
        ChatRequest(message=message)


def test_unknown_fields_and_unsupported_locale():
    with pytest.raises(ValidationError):
        ChatRequest(message="hello", user_id="spoofed")
    with pytest.raises(ValidationError):
        ChatRequest(message="hello", locale="unsupported")


def test_configuration_rejects_weak_tokens(settings):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=settings.database_url, api_clients={"x": "short"})


def test_configuration_hides_secrets(settings):
    assert "a" * 40 not in repr(settings)
    assert "test:test" not in repr(settings)


def test_liveness_and_auth_do_not_need_database(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json()["provider"] == "fake"
        response = client.post("/api/chat", json={"message": "hello"})
        assert response.status_code == 401


def test_readiness_failure_is_sanitized(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["retryable"] is True
        assert "test:test" not in response.text
