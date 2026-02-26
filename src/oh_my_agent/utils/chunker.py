from __future__ import annotations

MAX_CHUNK_SIZE = 1990


def chunk_message(text: str, max_size: int = MAX_CHUNK_SIZE) -> list[str]:
    """Split *text* into chunks of at most *max_size* characters.

    Splitting priority: paragraph break > newline > space > hard cut.
    """
    if len(text) <= max_size:
        return [text] if text.strip() else []

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_size:
            chunk = remaining.strip()
            if chunk:
                chunks.append(chunk)
            break

        split_at = _find_split_point(remaining, max_size)
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:]

    return chunks


def _find_split_point(text: str, max_size: int) -> int:
    for sep in ("\n\n", "\n", " "):
        idx = text.rfind(sep, 0, max_size)
        if idx > 0:
            return idx + len(sep)
    return max_size
