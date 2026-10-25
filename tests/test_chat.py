from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from conftest import TOKEN_B
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from service_assistant.api.app import create_app
from service_assistant.orchestration.engine import Orchestrator
from service_assistant.repositories.models import Conversation, IdempotencyRecord, Message

pytestmark = pytest.mark.integration


def test_multi_turn_persistence_and_retry(client, app, headers):
    request = {"message": "I need to pay my electricity bill"}
    first = client.post("/api/chat", json=request, headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "needs_clarification"
    retry = client.post("/api/chat", json=request, headers=headers)
    assert retry.json() == first.json()
    followup = client.post(
        "/api/chat",
        json={"conversation_id": first.json()["conversation_id"], "message": "Example Utility"},
        headers={**headers, "Idempotency-Key": "followup"},
    )
    assert followup.status_code == 200
    assert followup.json()["status"] == "no_verified_match"
    assert followup.json()["links"] == []
    with app.state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 4
        conversation = session.get(Conversation, UUID(first.json()["conversation_id"]))
        assert conversation.state["provider"] == "Example Utility"


def test_idempotency_payload_conflict(client, headers):
    client.post("/api/chat", json={"message": "hello"}, headers=headers)
    response = client.post("/api/chat", json={"message": "different"}, headers=headers)
    assert response.status_code == 409


def test_conversation_ownership(client, headers):
    first = client.post("/api/chat", json={"message": "electricity"}, headers=headers).json()
    response = client.post(
        "/api/chat",
        json={"message": "hello", "conversation_id": first["conversation_id"]},
        headers={**headers, "Authorization": f"Bearer {TOKEN_B}"},
    )
    assert response.status_code == 404


def test_expired_conversation(client, app, headers):
    first = client.post("/api/chat", json={"message": "hello"}, headers=headers).json()
    with app.state.sessions.begin() as session:
        conversation = session.get(Conversation, UUID(first["conversation_id"]))
        conversation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    response = client.post(
        "/api/chat",
        json={"conversation_id": first["conversation_id"], "message": "hello"},
        headers={**headers, "Idempotency-Key": "next"},
    )
    assert response.status_code == 410


@pytest.mark.parametrize("key", ["", "bad key", "a" * 129])
def test_invalid_idempotency_keys(client, headers, key):
    response = client.post(
        "/api/chat", json={"message": "hello"}, headers={**headers, "Idempotency-Key": key}
    )
    assert response.status_code == 422


def test_input_not_echoed_in_validation_error(client, headers):
    response = client.post(
        "/api/chat", json={"message": "", "password": "private"}, headers=headers
    )
    assert response.status_code == 422
    assert "private" not in response.text


def test_sensitive_input_not_persisted_or_logged(client, app, headers, caplog):
    with caplog.at_level("INFO", logger="service_assistant.requests"):
        response = client.post("/api/chat", json={"message": "my OTP is 123456"}, headers=headers)
    assert response.json()["status"] == "unsupported"
    with app.state.sessions() as session:
        assert all("123456" not in item for item in session.scalars(select(Message.text)))
        conversation = session.scalar(select(Conversation))
        assert "123456" not in str(conversation.state)
    assert "123456" not in caplog.text
    assert headers["Authorization"] not in caplog.text


def test_concurrent_duplicate_requests(client, app, headers):
    def send(_):
        return client.post("/api/chat", json={"message": "electricity"}, headers=headers)

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(send, range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json() == responses[0].json() for response in responses)
    with app.state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1
        assert session.scalar(select(func.count()).select_from(Message)) == 2


def test_failed_provider_rolls_back(database_url, settings, headers, app):
    class BrokenProvider:
        def interpret(self, message, state, locale="en-IN"):
            raise RuntimeError("sensitive upstream data")

    settings = settings.model_copy(
        update={"database_url": settings.database_url.__class__(database_url)}
    )
    app = create_app(settings, Orchestrator(BrokenProvider()))
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "electricity"}, headers=headers)
    assert response.status_code == 500
    assert "sensitive" not in response.text
    with app.state.sessions() as session:
        assert (
            session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == "test-request"))
            is None
        )


def test_ready(client):
    assert client.get("/health/ready").status_code == 200


def test_state_survives_application_restart(client, database_url, settings, headers):
    first = client.post("/api/chat", json={"message": "electricity"}, headers=headers).json()
    new_settings = settings.model_copy(
        update={"database_url": settings.database_url.__class__(database_url)}
    )
    with TestClient(create_app(new_settings)) as restarted:
        response = restarted.post(
            "/api/chat",
            json={"message": "Example Utility", "conversation_id": first["conversation_id"]},
            headers={**headers, "Idempotency-Key": "after-restart"},
        )
    assert response.json()["status"] == "no_verified_match"


def test_expired_idempotency_key_can_be_reused(client, app, headers):
    first = client.post("/api/chat", json={"message": "hello"}, headers=headers).json()
    with app.state.sessions.begin() as session:
        record = session.scalar(select(IdempotencyRecord))
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    response = client.post("/api/chat", json={"message": "electricity"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["conversation_id"] != first["conversation_id"]
