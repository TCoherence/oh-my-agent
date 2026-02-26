from oh_my_agent.utils.chunker import chunk_message, MAX_CHUNK_SIZE


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
