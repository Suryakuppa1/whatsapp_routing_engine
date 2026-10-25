import hashlib
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from service_assistant.domain.contracts import ChatRequest, ChatResponse
from service_assistant.infrastructure.config import Settings
from service_assistant.infrastructure.database import build_database
from service_assistant.infrastructure.llm.factory import build_intent_provider
from service_assistant.infrastructure.search.factory import build_search_provider
from service_assistant.orchestration.contracts import ProviderError
from service_assistant.orchestration.engine import Orchestrator
from service_assistant.services.chat import ApplicationError, ChatService
from service_assistant.services.discovery import ServiceDiscovery
from service_assistant.services.registry import load_registry
from service_assistant.tools.search import SearchError

logger = logging.getLogger("service_assistant.requests")
bearer = HTTPBearer(auto_error=False)


def create_app(
    settings: Settings | None = None, orchestrator: Orchestrator | None = None
) -> FastAPI:
    settings = settings or Settings()
    registry = load_registry(settings.service_registry_path) if orchestrator is None else None
    owned_search = build_search_provider(settings) if orchestrator is None else None
    discovery = ServiceDiscovery(registry, search=owned_search) if registry is not None else None
    db, sessions = build_database(settings.database_url.get_secret_value())
    owned_provider = build_intent_provider(settings) if orchestrator is None else None
    orchestrator = orchestrator or Orchestrator(owned_provider, discovery)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            close = getattr(owned_provider, "close", None)
            if close:
                close()
            close_search = getattr(owned_search, "close", None)
            if close_search:
                close_search()
            db.dispose()

    app = FastAPI(title="Service Assistant", version="0.6.0", lifespan=lifespan)
    app.state.sessions = sessions

    def error(request: Request, status: int, code: str, message: str, retryable: bool = False):
        return JSONResponse(
            status_code=status,
            content={
                "request_id": str(request.state.request_id),
                "error": {"code": code, "message": message, "retryable": retryable},
            },
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid4()
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # Do not log exception strings, headers, bodies, or URLs with query parameters.
            response = error(request, 500, "INTERNAL_ERROR", "An unexpected error occurred.")
        response.headers["X-Request-ID"] = str(request.state.request_id)
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": str(request.state.request_id),
                    "method": request.method,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )
        )
        return response

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError):
        return error(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default detail may echo sensitive input.
        return error(request, 422, "INVALID_REQUEST", "Check the request fields and headers.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error(request, exc.status_code, "HTTP_ERROR", "The request could not be processed.")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        return error(request, 503, "DATABASE_UNAVAILABLE", "Please try again shortly.", True)

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc: ProviderError):
        return error(
            request,
            503 if exc.retryable else 502,
            exc.code,
            "I couldn’t safely interpret that request. Please try again shortly.",
            exc.retryable,
        )

    @app.exception_handler(SearchError)
    async def search_error(request: Request, exc: SearchError):
        return error(
            request,
            503 if exc.retryable else 502,
            exc.code,
            "Service search is temporarily unavailable. Please try again shortly.",
            exc.retryable,
        )

    def authenticate(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        supplied = hashlib.sha256(
            (credentials.credentials if credentials else "").encode()
        ).digest()
        for owner, token in settings.api_clients.items():
            expected = hashlib.sha256(token.get_secret_value().encode()).digest()
            if credentials and secrets.compare_digest(supplied, expected):
                return owner
        raise ApplicationError(401, "UNAUTHORIZED", "A valid application token is required.")

    @app.get("/health/live")
    def live():
        return {"status": "ok", "provider": settings.llm_provider}

    @app.get("/health/ready")
    def ready():
        with sessions() as session:
            # Require the initial migrated schema as well as database connectivity.
            session.execute(text("SELECT id FROM conversations LIMIT 0"))
            session.execute(text("SELECT id FROM messages LIMIT 0"))
            session.execute(text("SELECT id FROM idempotency_records LIMIT 0"))
        return {"status": "ready"}

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(
        body: ChatRequest,
        request: Request,
        owner: Annotated[str, Depends(authenticate)],
        idempotency_key: Annotated[
            str, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
        ],
    ):
        with sessions.begin() as session:
            return ChatService(session, orchestrator, settings.session_ttl_seconds).chat(
                owner, idempotency_key, body, request.state.request_id
            )

    return app
