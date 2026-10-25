import json
import time

import httpx
from pydantic import SecretStr

from service_assistant.domain.contracts import ConversationState
from service_assistant.orchestration.contracts import IntentInterpretation, ProviderError
from service_assistant.orchestration.prompts import INTENT_INSTRUCTIONS, PROMPT_VERSION

MAX_RESPONSE_BYTES = 65536


class OpenAIIntentProvider:
    """Bounded Responses API adapter; no model-supplied tools or free-form replies."""

    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        timeout: float = 8,
        transport: httpx.BaseTransport | None = None,
    ):
        self._model, self._timeout = model, timeout
        self._client = httpx.Client(
            base_url="https://api.openai.com/v1/",
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def interpret(
        self, message: str, state: ConversationState, locale: str = "en-IN"
    ) -> IntentInterpretation:
        payload = {
            "model": self._model,
            "store": False,
            "max_output_tokens": 600,
            "instructions": INTENT_INSTRUCTIONS,
            "input": json.dumps(
                {
                    "message": message,
                    "previous_context": state.model_dump(
                        include={
                            "intent",
                            "provider",
                            "jurisdiction",
                            "service_detail",
                            "pending_field",
                        }
                    ),
                    "locale": locale,
                }
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "service_intent",
                    "strict": True,
                    "schema": IntentInterpretation.model_json_schema(),
                }
            },
            "metadata": {"prompt_version": PROMPT_VERSION},
        }
        started = time.monotonic()
        try:
            with self._client.stream("POST", "responses", json=payload) as response:
                if response.status_code != 200:
                    retryable = response.status_code in (408, 429) or response.status_code >= 500
                    raise ProviderError("PROVIDER_UNAVAILABLE", retryable)
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ProviderError("PROVIDER_INVALID_OUTPUT", False)
                    if time.monotonic() - started > self._timeout:
                        raise ProviderError("PROVIDER_TIMEOUT", True)
                    data.extend(chunk)
            envelope = json.loads(data)
            if envelope.get("status") != "completed":
                raise ProviderError("PROVIDER_INVALID_OUTPUT", False)
            if any(item.get("type") not in ("message", "reasoning") for item in envelope["output"]):
                raise ProviderError("PROVIDER_INVALID_OUTPUT", False)
            messages = [item for item in envelope["output"] if item.get("type") == "message"]
            if len(messages) != 1 or messages[0].get("role") != "assistant":
                raise ProviderError("PROVIDER_INVALID_OUTPUT", False)
            content = messages[0]["content"]
            if any(item.get("type") == "refusal" for item in content):
                raise ProviderError("PROVIDER_REFUSED", False)
            if len(content) != 1 or content[0].get("type") != "output_text":
                raise ProviderError("PROVIDER_INVALID_OUTPUT", False)
            return IntentInterpretation.model_validate_json(content[0]["text"])
        except httpx.TimeoutException:
            raise ProviderError("PROVIDER_TIMEOUT", True) from None
        except httpx.RequestError:
            raise ProviderError("PROVIDER_UNAVAILABLE", True) from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError("PROVIDER_INVALID_OUTPUT", False) from None
