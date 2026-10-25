from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr
    api_clients: dict[str, SecretStr] = Field(repr=False)
    session_ttl_seconds: int = Field(default=86400, ge=60, le=2592000)
    llm_provider: Literal["fake", "openai"] = "fake"
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="gpt-4.1-mini", min_length=1, max_length=100)
    llm_timeout_seconds: float = Field(default=8, ge=1, le=15)
    service_registry_path: Path | None = None

    @model_validator(mode="after")
    def validate_llm(self):
        if self.llm_provider == "openai":
            if not self.openai_api_key or not self.openai_api_key.get_secret_value().strip():
                raise ValueError("OPENAI_API_KEY is required for the openai provider")
        return self

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        if make_url(value.get_secret_value()).drivername != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must use postgresql+psycopg")
        return value

    @field_validator("api_clients")
    @classmethod
    def validate_clients(cls, clients: dict[str, SecretStr]) -> dict[str, SecretStr]:
        tokens = [v.get_secret_value() for v in clients.values()]
        if not clients or any(not name or len(name) > 100 for name in clients):
            raise ValueError("At least one named API client is required")
        if any(len(token) < 32 or token.startswith("replace-") for token in tokens):
            raise ValueError(
                "API client tokens must be unique random values of at least 32 characters"
            )
        if len(set(tokens)) != len(tokens):
            raise ValueError("API client tokens must be unique")
        return clients
