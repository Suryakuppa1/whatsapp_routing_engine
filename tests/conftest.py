import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from service_assistant.api.app import create_app
from service_assistant.infrastructure.config import Settings

TOKEN_A = "a" * 40
TOKEN_B = "b" * 40


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@127.0.0.1:1/test",
        api_clients={"alice": TOKEN_A, "bob": TOKEN_B},
    )


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated disposable PostgreSQL database")
    # Only the explicitly designated test database is migrated and cleared.
    previous = {key: os.environ.get(key) for key in ("DATABASE_URL", "API_CLIENTS")}
    os.environ["DATABASE_URL"] = url
    os.environ["API_CLIENTS"] = '{"test":"' + TOKEN_A + '"}'
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        command.check(config)
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return url


@pytest.fixture
def app(database_url, settings):
    db = create_engine(database_url)
    with db.begin() as connection:
        connection.execute(text("TRUNCATE messages, conversations, idempotency_records CASCADE"))
    db.dispose()
    return create_app(
        settings.model_copy(update={"database_url": settings.database_url.__class__(database_url)})
    )


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def headers():
    return {"Authorization": f"Bearer {TOKEN_A}", "Idempotency-Key": "test-request"}
