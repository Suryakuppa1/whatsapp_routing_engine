from uuid import uuid4

import pytest

from service_assistant.channels.rendering import render_plain_messages
from service_assistant.domain.contracts import ChatResponse


def test_long_unicode_messages_preserve_text_within_platform_budget():
    text = "To: care@example.com\n" + "🙂తెలుగు" * 1000 + "\nRegards,\nSurya"
    response = ChatResponse(
        status="unsupported", message=text, request_id=uuid4(), conversation_id=uuid4()
    )
    chunks = render_plain_messages(response)
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 3500 for chunk in chunks)


def test_invalid_chunk_budget():
    response = ChatResponse(
        status="unsupported", message="hello", request_id=uuid4(), conversation_id=uuid4()
    )
    with pytest.raises(ValueError):
        render_plain_messages(response, max_length=0)
