"""Tests for the per-day markdown session diary writer."""

from __future__ import annotations

from datetime import datetime

import pytest

from oh_my_agent.memory.session_diary import SessionDiaryWriter, strip_system_blocks


@pytest.mark.asyncio
async def test_diary_writes_per_day_file(tmp_path) -> None:
    writer = SessionDiaryWriter(tmp_path)
    writer.start()
    ts = datetime(2026, 4, 24, 14, 3, 21)
    await writer.append(
        role="user",
        platform="discord",
        channel_id="12345",
        thread_id="t9",
        author="alice",
        content="hello",
        ts=ts,
    )
    await writer.stop()

    expected = tmp_path / "2026-04-24.md"
    assert expected.exists()
    body = expected.read_text(encoding="utf-8")
    assert "## 14:03:21 · discord#12345 · thread:t9 · user:alice" in body
    assert "> hello" in body


@pytest.mark.asyncio
async def test_diary_preserves_append_order(tmp_path) -> None:
    writer = SessionDiaryWriter(tmp_path)
    writer.start()
    ts = datetime(2026, 4, 24, 14, 3, 21)
    for i in range(5):
        await writer.append(
            role="user",
            platform="discord",
            channel_id="c",
            thread_id="t",
            author="u",
            content=f"msg-{i}",
            ts=ts,
        )
    await writer.stop()

    body = (tmp_path / "2026-04-24.md").read_text(encoding="utf-8")
    # Each message should appear in the file in the order it was enqueued.
    indices = [body.index(f"> msg-{i}") for i in range(5)]
    assert indices == sorted(indices)


@pytest.mark.asyncio
async def test_diary_formats_user_vs_assistant(tmp_path) -> None:
    writer = SessionDiaryWriter(tmp_path)
    writer.start()
    ts = datetime(2026, 4, 24, 9, 0, 0)
    await writer.append(
        role="user",
        platform="discord",
        channel_id="c",
        thread_id="t",
        author="alice",
        content="ask me",
        ts=ts,
    )
    await writer.append(
        role="assistant",
        platform="discord",
        channel_id="c",
        thread_id="t",
        author="claude",
        content="plain reply",
        ts=ts,
    )
    await writer.stop()

    body = (tmp_path / "2026-04-24.md").read_text(encoding="utf-8")
    # User content is quoted, assistant content isn't.
    assert "> ask me" in body
    assert "\nplain reply\n" in body


@pytest.mark.asyncio
async def test_diary_splits_files_across_days(tmp_path) -> None:
    writer = SessionDiaryWriter(tmp_path)
    writer.start()
    await writer.append(
        role="user",
        platform="discord",
        channel_id="c",
        thread_id="t",
        author="u",
        content="day-1",
        ts=datetime(2026, 4, 24, 23, 59, 59),
    )
    await writer.append(
        role="user",
        platform="discord",
        channel_id="c",
        thread_id="t",
        author="u",
        content="day-2",
        ts=datetime(2026, 4, 25, 0, 0, 1),
    )
    await writer.stop()

    day1 = (tmp_path / "2026-04-24.md").read_text(encoding="utf-8")
    day2 = (tmp_path / "2026-04-25.md").read_text(encoding="utf-8")
    assert "day-1" in day1 and "day-2" not in day1
    assert "day-2" in day2 and "day-1" not in day2


@pytest.mark.asyncio
async def test_diary_stop_drains_pending_entries(tmp_path) -> None:
    writer = SessionDiaryWriter(tmp_path)
    writer.start()
    # Enqueue many entries back-to-back then stop immediately.
    ts = datetime(2026, 4, 24, 12, 0, 0)
    for i in range(20):
        await writer.append(
            role="assistant",
            platform="discord",
            channel_id="c",
            thread_id="t",
            author="claude",
            content=f"entry-{i}",
            ts=ts,
        )
    await writer.stop()
    body = (tmp_path / "2026-04-24.md").read_text(encoding="utf-8")
    for i in range(20):
        assert f"entry-{i}" in body


@pytest.mark.asyncio
async def test_diary_quotes_empty_user_content(tmp_path) -> None:
    writer = SessionDiaryWriter(tmp_path)
    writer.start()
    await writer.append(
        role="user",
        platform="discord",
        channel_id="c",
        thread_id="t",
        author="alice",
        content="",
        ts=datetime(2026, 4, 24, 8, 0, 0),
    )
    await writer.stop()
    body = (tmp_path / "2026-04-24.md").read_text(encoding="utf-8")
    assert "> (empty)" in body


@pytest.mark.asyncio
async def test_diary_append_auto_starts_worker(tmp_path) -> None:
    writer = SessionDiaryWriter(tmp_path)
    # No explicit .start() — append should auto-start.
    await writer.append(
        role="user",
        platform="discord",
        channel_id="c",
        thread_id="t",
        author="alice",
        content="auto",
        ts=datetime(2026, 4, 24, 1, 2, 3),
    )
    await writer.stop()
    body = (tmp_path / "2026-04-24.md").read_text(encoding="utf-8")
    assert "> auto" in body


@pytest.mark.asyncio
async def test_diary_append_after_stop_is_noop(tmp_path) -> None:
    writer = SessionDiaryWriter(tmp_path)
    writer.start()
    await writer.stop()
    # Post-stop appends should silently skip rather than crash.
    await writer.append(
        role="user",
        platform="discord",
        channel_id="c",
        thread_id="t",
        author="alice",
        content="ignored",
        ts=datetime(2026, 4, 24, 10, 0, 0),
    )
    # No file should be written since the worker is dead.
    assert not (tmp_path / "2026-04-24.md").exists()


# ── strip_system_blocks ───────────────────────────────────────────── #


def _user_block(time: str = "14:03:21", author: str = "alice") -> str:
    return (
        f"## {time} · discord#100 · thread:t · user:{author}\n"
        f"> hi from user"
    )


def _assistant_block(time: str = "14:03:24") -> str:
    return (
        f"## {time} · discord#100 · thread:t · assistant:claude\n"
        f"agent reply"
    )


def _system_block(time: str = "14:03:30") -> str:
    return (
        f"## {time} · discord#100 · thread:t · system:runtime\n"
        f"Task `abc123` queued.\n\n**Output detail**\nrich automation result"
    )


def test_strip_system_blocks_removes_system_entries():
    diary = "\n\n".join([_user_block(), _system_block(), _assistant_block()])
    out = strip_system_blocks(diary)
    assert "user:alice" in out
    assert "assistant:claude" in out
    assert "system:runtime" not in out
    assert "rich automation result" not in out


def test_strip_system_blocks_handles_first_block_no_prefix():
    # A diary file content always starts with the first ``##`` header — no
    # leading blank line. The split regex must still produce all blocks.
    diary = _system_block() + "\n\n" + _user_block()
    out = strip_system_blocks(diary)
    assert "system:runtime" not in out
    assert "user:alice" in out


def test_strip_system_blocks_empty_or_no_system():
    assert strip_system_blocks("") == ""
    user_only = _user_block()
    assert strip_system_blocks(user_only) == user_only


def test_strip_system_blocks_all_system_returns_empty():
    diary = "\n\n".join([_system_block("10:00:00"), _system_block("11:00:00")])
    out = strip_system_blocks(diary)
    # All blocks dropped → empty
    assert out.strip() == ""
