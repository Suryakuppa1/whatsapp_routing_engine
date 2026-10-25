from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from test_handoff import REVIEW_TIME

from service_assistant.api.app import create_app
from service_assistant.orchestration.engine import FakeIntentProvider, Orchestrator
from service_assistant.repositories.models import Conversation, Message
from service_assistant.services.discovery import ServiceDiscovery
from service_assistant.services.registry import load_registry

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("decision,outcome", [("yes", "answered"), ("no", "cancelled")])
def test_apcpdcl_conversation_confirmation_survives_restart(
    app, database_url, settings, headers, decision, outcome
):
    configured = settings.model_copy(update={"database_url": SecretStr(database_url)})
    registry = load_registry(Path("registry/apcpdcl.json"))

    def make_app():
        return create_app(
            configured,
            Orchestrator(
                FakeIntentProvider(), ServiceDiscovery(registry, clock=lambda: REVIEW_TIME)
            ),
        )

    with TestClient(make_app()) as client:
        first = client.post("/api/chat", json={"message": "electricity"}, headers=headers).json()
        cid = first["conversation_id"]
        client.post(
            "/api/chat",
            json={"message": "APCPDCL", "conversation_id": cid},
            headers={**headers, "Idempotency-Key": "provider"},
        )
        awaiting = client.post(
            "/api/chat",
            json={
                "message": "Andhra Pradesh",
                "conversation_id": cid,
            },
            headers={**headers, "Idempotency-Key": "location"},
        )
        assert awaiting.json()["handoff"]["status"] == "awaiting_confirmation"
        assert awaiting.json()["links"] == []
    with TestClient(make_app()) as restarted:
        unexpected = restarted.post(
            "/api/chat",
            json={
                "message": "12345678901",
                "conversation_id": cid,
            },
            headers={**headers, "Idempotency-Key": "unexpected-input"},
        )
        assert unexpected.json()["status"] == "needs_clarification"
        assert unexpected.json()["links"] == []
        with app.state.sessions() as session:
            assert all("12345678901" not in text for text in session.scalars(select(Message.text)))
        body = {"message": decision, "conversation_id": cid}
        consent_headers = {**headers, "Idempotency-Key": "decision"}
        response = restarted.post("/api/chat", json=body, headers=consent_headers)
        assert response.status_code == 200
        assert response.json()["status"] == outcome
        assert bool(response.json()["links"]) == (decision == "yes")
        assert (
            restarted.post("/api/chat", json=body, headers=consent_headers).json()
            == response.json()
        )
    with app.state.sessions() as session:
        state = session.scalar(select(Conversation)).state
        assert state["pending_handoff_service_id"] is None
