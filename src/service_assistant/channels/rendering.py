from service_assistant.domain.contracts import ChatResponse


def render_plain_messages(reply: ChatResponse, max_length: int = 3500) -> tuple[str, ...]:
    """Plain-text chunks for future WhatsApp/Telegram delivery; no HTML/Markdown parsing."""
    if max_length < 1:
        raise ValueError("Message length must be positive")
    text, chunks = reply.message, []
    while text:
        units, end, last_newline = 0, 0, 0
        for char in text:
            cost = 2 if ord(char) > 0xFFFF else 1
            if units + cost > max_length:
                break
            units += cost
            end += 1
            if char == "\n":
                last_newline = end
        if end == 0:
            raise ValueError("Message length is too small for a Unicode character")
        if end < len(text) and last_newline:
            end = last_newline
        chunks.append(text[:end])
        text = text[end:]
    return tuple(chunks)
