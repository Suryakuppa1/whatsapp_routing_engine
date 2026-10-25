import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from test_complaints import registry_and_clock

from service_assistant.api.app import create_app
from service_assistant.channels.rendering import render_plain_messages
from service_assistant.domain.contracts import ChatResponse
from service_assistant.orchestration.engine import FakeIntentProvider, Orchestrator
from service_assistant.repositories.models import Conversation
from service_assistant.services.discovery import ServiceDiscovery

pytestmark = pytest.mark.integration


def test_complaint_draft_persistence_and_channel_ready_text(app, database_url, settings, headers):
    registry, now = registry_and_clock()
    discovery = ServiceDiscovery(registry, clock=lambda: now)
    configured = settings.model_copy(update={"database_url": SecretStr(database_url)})

    def new_app():
        return create_app(configured, Orchestrator(FakeIntentProvider(), discovery))

    with TestClient(new_app()) as client:
        cid = None
        for index, message in enumerate(
            [
                "Please compose a complaint email",
                "APCPDCL",
                "Andhra Pradesh",
                "The electricity supply in my area is unavailable since yesterday.",
            ]
        ):
            response = client.post(
                "/api/chat",
                json={"message": message, "conversation_id": cid},
                headers={**headers, "Idempotency-Key": str(index)},
            )
            assert response.status_code == 200
            cid = response.json()["conversation_id"]
    with TestClient(new_app()) as restarted:
        body = {"message": "Surya", "conversation_id": cid}
        result = restarted.post("/api/chat", json=body, headers=headers)
        assert result.status_code == 200
        assert result.json()["email_draft"]["delivery_status"] == "not_sent"
        assert result.json()["email_draft"]["to"] == "customercare@apcpdcl.in"
        assert restarted.post("/api/chat", json=body, headers=headers).json() == result.json()
        draft_response = ChatResponse.model_validate(result.json())
        chunks = render_plain_messages(draft_response, max_length=150)
        assert all(len(chunk) <= 150 for chunk in chunks)
        assert "".join(chunks) == result.json()["message"]
        discovery.clock = lambda: now.replace(year=now.year + 1)
        expired = restarted.post("/api/chat", json=body, headers=headers)
        assert expired.status_code == 409
        assert expired.json()["error"]["code"] == "VERIFICATION_EXPIRED"
    with app.state.sessions() as session:
        assert session.scalar(select(Conversation)).state["complaint_sender_name"] == "Surya"
