"""Discord-safe message chunker with fenced code-block awareness.

Splits long messages into ≤ ``MAX_CHUNK_SIZE`` chunks without
breaking fenced code blocks (````` `` ``` ````` / ``~~~``).  Oversized code
blocks are split by line with fence close / re-open so every chunk
renders correctly in Markdown.

Plain-text splitting priority: paragraph break > newline > space > hard cut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHUNK_SIZE = 1990

# Matches an opening or closing fence line.
# group(1) = fence chars (```, ~~~, …)
# group(2) = optional info-string (language tag) — empty for closing fences.
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})\s*(\S*)\s*$", re.MULTILINE)


# ── Internal data structures ──────────────────────────────────────────── #


@dataclass
class _Segment:
    text: str
    is_code: bool
    lang: str  # meaningful only for code blocks


def _parse_segments(text: str) -> list[_Segment]:
    """Split *text* into alternating plain-text / fenced-code-block segments."""
    segments: list[_Segment] = []
    pos = 0
    in_code = False
    fence_char = ""
    fence_len = 0
    code_start = 0
    lang = ""

    for m in _FENCE_RE.finditer(text):
        if not in_code:
            # ── opening fence ────────────────────────────────────────
            fence_char = m.group(1)[0]
            fence_len = len(m.group(1))
            lang = m.group(2)
            if m.start() > pos:
                segments.append(_Segment(text[pos:m.start()], False, ""))
            code_start = m.start()
            in_code = True
        else:
            # ── potential closing fence ──────────────────────────────
            if (
                m.group(1)[0] == fence_char
                and len(m.group(1)) >= fence_len
                and not m.group(2)
            ):
                end = m.end()
                segments.append(_Segment(text[code_start:end], True, lang))
                pos = end
                in_code = False
            # else: not a matching close — skip

    # Trailing content
    if in_code:
        # Unclosed block extends to end of text
        segments.append(_Segment(text[code_start:], True, lang))
    elif pos < len(text):
        segments.append(_Segment(text[pos:], False, ""))

    if not segments:
        segments.append(_Segment(text, False, ""))

    return segments


# ── Public API ─────────────────────────────────────────────────────────── #


def chunk_message(text: str, max_size: int = MAX_CHUNK_SIZE) -> list[str]:
    """Split *text* into chunks of at most *max_size* characters.

    Fenced code blocks (````` `` ``` ````` / ``~~~``) are kept intact when
    possible.  Oversized blocks are split by line with fence close / re-open.
    Plain-text splitting priority: paragraph break > newline > space > hard cut.
    """
    if len(text) <= max_size:
        return [text] if text.strip() else []

    segments = _parse_segments(text)
    chunks: list[str] = []
    buf = ""

    for seg in segments:
        combined = buf + seg.text
        if len(combined) <= max_size:
            buf = combined
            continue

        # Doesn't fit — flush current buffer
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

        if len(seg.text) <= max_size:
            # Segment fits on its own — start a new buffer
            buf = seg.text
        elif seg.is_code:
            # Oversized code block — split with fence close/re-open
            chunks.extend(_split_code_block(seg.text, seg.lang, max_size))
        else:
            # Oversized plain text — split using paragraph/newline/space heuristic
            sub = _chunk_plain_text(seg.text, max_size)
            if sub:
                chunks.extend(sub[:-1])
                buf = sub[-1]  # last piece may merge with next segment

    # Flush remaining buffer
    if buf.strip():
        if len(buf) > max_size:
            chunks.extend(_chunk_plain_text(buf, max_size))
        else:
            chunks.append(buf.strip())

    return chunks


# ── Code-block splitting ──────────────────────────────────────────────── #


def _split_code_block(block_text: str, lang: str, max_size: int) -> list[str]:
    """Split an oversized fenced code block, closing and re-opening the fence.

    Each resulting chunk is a valid fenced code block with the same
    language tag.
    """
    lines = block_text.split("\n")

    open_fence = lines[0]  # e.g. "```python"
    content_lines = lines[1:]

    # Detect closing fence
    close_fence = ""
    if content_lines and _FENCE_RE.match(content_lines[-1]):
        close_fence = content_lines[-1]
        content_lines = content_lines[:-1]

    if not close_fence:
        m = _FENCE_RE.match(open_fence)
        close_fence = m.group(1) if m else "```"

    # Per-chunk overhead: opening + closing fences plus two newlines
    overhead = len(open_fence) + len(close_fence) + 2
    available = max_size - overhead
    if available <= 0:
        available = max_size // 2

    chunks: list[str] = []
    buf_lines: list[str] = []
    buf_size = 0

    for line in content_lines:
        line_len = len(line) + 1  # +1 for the \n separator
        if buf_size + line_len > available and buf_lines:
            chunk = open_fence + "\n" + "\n".join(buf_lines) + "\n" + close_fence
            chunks.append(chunk)
            buf_lines = []
            buf_size = 0
        buf_lines.append(line)
        buf_size += line_len

    if buf_lines:
        chunk = open_fence + "\n" + "\n".join(buf_lines) + "\n" + close_fence
        chunks.append(chunk)

    return chunks if chunks else [block_text]


# ── Plain-text splitting (original algorithm) ─────────────────────────── #


def _chunk_plain_text(text: str, max_size: int) -> list[str]:
    """Split plain text using paragraph > newline > space > hard cut priority."""
    if len(text) <= max_size:
        chunk = text.strip()
        return [chunk] if chunk else []

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
    """Find the best split point within *max_size* characters."""
    for sep in ("\n\n", "\n", " "):
        idx = text.rfind(sep, 0, max_size)
        if idx > 0:
            return idx + len(sep)
    return max_size
