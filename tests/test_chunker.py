from oh_my_agent.utils.chunker import (
    MAX_CHUNK_SIZE,
    _parse_segments,
    chunk_message,
)

# ── Original plain-text tests (backward compatibility) ───────────────── #


def test_short_message_returned_as_single_chunk():
    assert chunk_message("hello world") == ["hello world"]


def test_empty_string_returns_empty_list():
    assert chunk_message("") == []


def test_whitespace_only_returns_empty_list():
    assert chunk_message("   \n  ") == []


def test_exact_max_size_not_split():
    text = "x" * MAX_CHUNK_SIZE
    chunks = chunk_message(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_long_message_split_into_multiple_chunks():
    text = "x" * (MAX_CHUNK_SIZE * 2 + 100)
    chunks = chunk_message(text)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= MAX_CHUNK_SIZE


def test_splits_on_paragraph_boundary():
    para1 = "a" * 1800
    para2 = "b" * 400
    text = para1 + "\n\n" + para2
    chunks = chunk_message(text)
    assert len(chunks) == 2
    assert chunks[0].endswith(para1.rstrip())
    assert chunks[1] == para2


def test_splits_on_newline_when_no_paragraph():
    line1 = "a" * 1800
    line2 = "b" * 400
    text = line1 + "\n" + line2
    chunks = chunk_message(text)
    assert len(chunks) == 2


def test_splits_on_space_when_no_newline():
    # A single word longer than max — falls back to hard cut
    word = "a" * (MAX_CHUNK_SIZE + 10)
    chunks = chunk_message(word)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= MAX_CHUNK_SIZE


def test_all_chunks_together_equal_original_content():
    text = "\n\n".join(["paragraph " + str(i) + " " + ("x" * 300) for i in range(10)])
    chunks = chunk_message(text)
    assert len(chunks) > 1
    reassembled = " ".join(chunks)
    # Every word from original should appear somewhere in the output
    for word in text.split():
        assert word in reassembled


def test_custom_max_size():
    text = "hello world foo bar"
    chunks = chunk_message(text, max_size=10)
    assert all(len(c) <= 10 for c in chunks)


# ── Code-block awareness tests (Session 1D) ─────────────────────────── #


def test_code_block_fits_in_one_chunk():
    """Text with a code block that fits entirely — no split."""
    text = "Hello\n```python\nprint('hi')\n```\nGoodbye"
    chunks = chunk_message(text)
    assert len(chunks) == 1
    assert "```python" in chunks[0]
    assert "```" in chunks[0]


def test_split_avoids_code_block_boundary():
    """Split point that would fall inside a code block → split before/after."""
    pre = "a" * 1970
    code = "```python\nx = 1\n```"
    text = pre + "\n\n" + code  # 1970 + 2 + 19 = 1991 > 1990
    chunks = chunk_message(text)
    assert len(chunks) == 2
    # Code block should be intact in one chunk
    code_chunk = [c for c in chunks if "```python" in c]
    assert len(code_chunk) == 1
    assert "x = 1" in code_chunk[0]


def test_oversized_code_block_split_with_fence_reopen():
    """Code block exceeding max_size → split by lines, fence close/re-open."""
    max_s = 200
    lines = [f"line_{i} = {i}" for i in range(30)]
    code = "```python\n" + "\n".join(lines) + "\n```"
    assert len(code) > max_s

    chunks = chunk_message(code, max_size=max_s)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.startswith("```python\n"), f"Missing opening fence: {chunk!r}"
        assert chunk.endswith("\n```"), f"Missing closing fence: {chunk!r}"
        assert len(chunk) <= max_s


def test_code_block_language_tag_preserved():
    """Language tag is preserved across splits of oversized code blocks."""
    max_s = 150
    code = "```javascript\n" + "\n".join([f"var x{i} = {i};" for i in range(20)]) + "\n```"
    chunks = chunk_message(code, max_size=max_s)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert "```javascript" in chunk


def test_multiple_code_blocks():
    """Multiple code blocks in one message — each stays intact."""
    block1 = "```python\nprint(1)\n```"
    block2 = "```bash\necho hi\n```"
    text = block1 + "\n\nSome text in between\n\n" + block2
    chunks = chunk_message(text)
    # Should be one chunk if it fits
    assert len(chunks) == 1
    assert "```python" in chunks[0]
    assert "```bash" in chunks[0]


def test_multiple_code_blocks_split():
    """Multiple code blocks force a split — each block stays intact."""
    pre = "a" * 1970
    block = "```python\nprint(1)\n```"
    text = pre + "\n\n" + block  # 1970 + 2 + 22 = 1994 > 1990
    chunks = chunk_message(text)
    assert len(chunks) == 2
    code_chunk = [c for c in chunks if "```python" in c]
    assert len(code_chunk) == 1
    assert "print(1)" in code_chunk[0]


def test_code_block_at_end_of_message():
    """Code block at the very end is kept intact."""
    pre = "a" * 1800
    code = "```\nsome code\n```"
    text = pre + "\n\n" + code
    chunks = chunk_message(text)
    assert chunks[-1].strip().endswith("```")
    assert "some code" in chunks[-1]


def test_code_block_at_start_of_message():
    """Code block at the very start is kept intact."""
    code = "```\nsome code\n```"
    post = "\n\n" + "b" * 1800
    text = code + post
    chunks = chunk_message(text)
    assert chunks[0].startswith("```")
    assert "some code" in chunks[0]


def test_nested_backticks_in_code_block():
    """4-backtick fence can contain 3-backtick lines without breaking."""
    inner = "````python\nhere is ```triple``` inside\n````"
    # The inner ``` is NOT a fence because the outer fence uses 4 backticks
    segs = _parse_segments(inner)
    code_segs = [s for s in segs if s.is_code]
    assert len(code_segs) == 1
    assert "```triple```" in code_segs[0].text


def test_empty_code_block():
    """Empty code block is kept intact."""
    text = "Before\n```\n```\nAfter"
    chunks = chunk_message(text)
    assert len(chunks) == 1
    assert "```\n```" in chunks[0]


def test_tilde_fence():
    """Tilde fences (~~~) are handled the same as backtick fences."""
    text = "Hello\n~~~python\ncode()\n~~~\nBye"
    segs = _parse_segments(text)
    code_segs = [s for s in segs if s.is_code]
    assert len(code_segs) == 1
    assert "~~~python" in code_segs[0].text
