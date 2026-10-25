import json
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from service_assistant.api.app import create_app
from service_assistant.infrastructure.llm.openai import OpenAIIntentProvider
from service_assistant.orchestration.engine import Orchestrator
from service_assistant.repositories.models import Conversation, IdempotencyRecord, Message

pytestmark = pytest.mark.integration


def result(**updates):
    data = {
        "intent": "government_form",
        "provider": None,
        "jurisdiction": None,
        "service_detail": None,
        "action": "information",
        **updates,
    }
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": json.dumps(data)}],
                }
            ],
        },
    )


@pytest.fixture
def provider_client(app, settings, database_url):
    clients = []

    def make(handler):
        provider = OpenAIIntentProvider(
            SecretStr("test-only-secret"), "test-model", transport=httpx.MockTransport(handler)
        )
        configured = settings.model_copy(update={"database_url": SecretStr(database_url)})
        new_app = create_app(configured, Orchestrator(provider))
        client = TestClient(new_app)
        client.__enter__()
        clients.append((client, provider))
        return client

    yield make
    for client, provider in clients:
        client.__exit__(None, None, None)
        provider.close()


def test_llm_form_flow_and_idempotency(provider_client, headers, app):
    outputs = iter(
        [
            result(),
            result(jurisdiction="India"),
            result(jurisdiction="India", service_detail="passport"),
        ]
    )
    calls = []

    def handler(request):
        calls.append(request)
        return next(outputs)

    client = provider_client(handler)
    first = client.post("/api/chat", json={"message": "Find a government form"}, headers=headers)
    assert first.json()["clarification"]["field"] == "jurisdiction"
    duplicate = client.post(
        "/api/chat", json={"message": "Find a government form"}, headers=headers
    )
    assert duplicate.json() == first.json()
    conversation_id = first.json()["conversation_id"]
    second = client.post(
        "/api/chat",
        json={
            "message": "India",
            "conversation_id": conversation_id,
        },
        headers={**headers, "Idempotency-Key": "second"},
    )
    assert second.json()["clarification"]["field"] == "service_detail"
    third = client.post(
        "/api/chat",
        json={
            "message": "passport",
            "conversation_id": conversation_id,
        },
        headers={**headers, "Idempotency-Key": "third"},
    )
    assert third.json()["status"] == "no_verified_match"
    assert len(calls) == 3
    with app.state.sessions() as session:
        state = session.get(Conversation, UUID(conversation_id)).state
        assert state["service_detail"] == "passport"
        assert state["jurisdiction"] == "India"


@pytest.mark.parametrize(
    "response,status,code,retryable",
    [
        (httpx.Response(429, text="private upstream"), 503, "PROVIDER_UNAVAILABLE", True),
        (httpx.Response(200, text="private output"), 502, "PROVIDER_INVALID_OUTPUT", False),
        (result(provider="Invented"), 502, "PROVIDER_UNGROUNDED_OUTPUT", False),
    ],
)
def test_provider_failures_are_safe_and_retry_does_not_duplicate(
    provider_client, headers, app, response, status, code, retryable
):
    outputs = iter([response, result()])
    client = provider_client(lambda _: next(outputs))
    failed = client.post("/api/chat", json={"message": "Find a form"}, headers=headers)
    assert failed.status_code == status
    assert failed.json()["error"]["code"] == code
    assert failed.json()["error"]["retryable"] is retryable
    assert "private" not in failed.text
    with app.state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 0
        assert session.scalar(select(func.count()).select_from(Message)) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0
    retried = client.post("/api/chat", json={"message": "Find a form"}, headers=headers)
    assert retried.status_code == 200


def test_sensitive_input_never_reaches_provider(provider_client, headers):
    calls = []

    def handler(request):
        calls.append(request)
        return result()

    client = provider_client(handler)
    response = client.post("/api/chat", json={"message": "my OTP is 123456"}, headers=headers)
    assert response.json()["status"] == "unsupported"
    assert calls == []
