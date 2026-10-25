import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_registry import registry_data

from service_assistant.api.app import create_app
from service_assistant.repositories.models import IdempotencyRecord

pytestmark = pytest.mark.integration


def test_registry_chat_and_cache_revocation(app, database_url, settings, headers, tmp_path):
    data = registry_data(datetime.now(UTC))
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(data))
    configured = settings.model_copy(
        update={
            "database_url": settings.database_url.__class__(database_url),
            "service_registry_path": path,
        }
    )
    with TestClient(create_app(configured)) as client:
        first = client.post("/api/chat", json={"message": "electricity"}, headers=headers).json()
        conversation_id = first["conversation_id"]
        second = client.post(
            "/api/chat",
            json={
                "message": "Fixture Utility",
                "conversation_id": conversation_id,
            },
            headers={**headers, "Idempotency-Key": "provider"},
        )
        assert second.json()["clarification"]["field"] == "jurisdiction"
        body = {"message": "Testland", "conversation_id": conversation_id}
        final_headers = {**headers, "Idempotency-Key": "location"}
        answered = client.post("/api/chat", json=body, headers=final_headers)
        assert answered.status_code == 200
        assert answered.json()["status"] == "answered"
        assert answered.json()["links"][0]["verification"] == "registry_verified"
        assert answered.json()["links"][0]["reachability"] == "not_checked"
        assert client.post("/api/chat", json=body, headers=final_headers).json() == answered.json()
    with app.state.sessions() as session:
        record = session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.key == "location")
        )
        assert record.response["links"][0]["source_id"] == "fixture-utility"
    # Deploy a registry revision with revoked trust; old cached links must not escape.
    data["sources"][0]["enabled"] = False
    path.write_text(json.dumps(data))
    with TestClient(create_app(configured)) as restarted:
        response = restarted.post("/api/chat", json=body, headers=final_headers)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "VERIFICATION_EXPIRED"
        assert "links" not in response.json()


def test_invalid_registry_fails_startup(settings, tmp_path):
    path = tmp_path / "bad-registry.json"
    path.write_text('{"version":"broken","services":[{"id":"missing-fields"}]}')
    with pytest.raises(ValueError):
        create_app(settings.model_copy(update={"service_registry_path": path}))
