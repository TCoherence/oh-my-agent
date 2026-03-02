"""Tests for image attachment support across the pipeline."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.gateway.base import Attachment, IncomingMessage
from oh_my_agent.gateway.session import ChannelSession


# ---------------------------------------------------------------------------
# Attachment.is_image
# ---------------------------------------------------------------------------

def test_attachment_is_image_true_for_png():
    a = Attachment("img.png", "image/png", Path("/tmp/img.png"), "http://x", 100)
    assert a.is_image is True


def test_attachment_is_image_true_for_jpeg():
    a = Attachment("photo.jpg", "image/jpeg", Path("/tmp/photo.jpg"), "http://x", 200)
    assert a.is_image is True


def test_attachment_is_image_true_for_webp():
    a = Attachment("pic.webp", "image/webp", Path("/tmp/pic.webp"), "http://x", 300)
    assert a.is_image is True


def test_attachment_is_image_false_for_pdf():
    a = Attachment("doc.pdf", "application/pdf", Path("/tmp/doc.pdf"), "http://x", 400)
    assert a.is_image is False


def test_attachment_is_image_false_for_text():
    a = Attachment("file.txt", "text/plain", Path("/tmp/file.txt"), "http://x", 50)
    assert a.is_image is False


# ---------------------------------------------------------------------------
# _download_discord_attachments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_skips_non_image():
    """Non-image attachments should be filtered out."""
    from oh_my_agent.gateway.platforms.discord import _download_discord_attachments

    att = MagicMock()
    att.content_type = "application/pdf"
    att.size = 100
    att.filename = "doc.pdf"

    result = await _download_discord_attachments([att])
    assert result == []


@pytest.mark.asyncio
async def test_download_skips_oversized():
    """Images over 10 MB should be skipped."""
    from oh_my_agent.gateway.platforms.discord import (
        _MAX_IMAGE_BYTES,
        _download_discord_attachments,
    )

    att = MagicMock()
    att.content_type = "image/png"
    att.size = _MAX_IMAGE_BYTES + 1
    att.filename = "huge.png"

    result = await _download_discord_attachments([att])
    assert result == []


@pytest.mark.asyncio
async def test_download_handles_save_failure():
    """A failing save() should be gracefully skipped."""
    from oh_my_agent.gateway.platforms.discord import _download_discord_attachments

    att = MagicMock()
    att.content_type = "image/png"
    att.size = 100
    att.filename = "fail.png"
    att.url = "http://example.com/fail.png"
    att.save = AsyncMock(side_effect=Exception("network error"))

    result = await _download_discord_attachments([att])
    assert result == []


@pytest.mark.asyncio
async def test_download_success(tmp_path):
    """Successful download returns an Attachment with local_path."""
    from oh_my_agent.gateway.platforms.discord import _download_discord_attachments

    att = MagicMock()
    att.content_type = "image/jpeg"
    att.size = 500
    att.filename = "photo.jpg"
    att.url = "http://example.com/photo.jpg"

    # Simulate save by actually writing a file
    async def fake_save(dest):
        dest.write_bytes(b"fake image data")

    att.save = AsyncMock(side_effect=fake_save)

    result = await _download_discord_attachments([att])
    assert len(result) == 1
    assert result[0].filename == "photo.jpg"
    assert result[0].content_type == "image/jpeg"
    assert result[0].local_path.exists()
    assert result[0].is_image is True
    # cleanup
    result[0].local_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# ClaudeAgent._augment_prompt_with_images
# ---------------------------------------------------------------------------

def test_claude_augment_prompt_with_images_no_workspace(tmp_path):
    from oh_my_agent.agents.cli.claude import ClaudeAgent

    agent = ClaudeAgent()
    img = tmp_path / "test.png"
    img.write_bytes(b"PNG")

    result = agent._augment_prompt_with_images("describe this", [img], cwd=None)
    assert str(img) in result
    assert "describe this" in result
    assert "Read the image file" in result


def test_claude_augment_prompt_with_images_with_workspace(tmp_path):
    from oh_my_agent.agents.cli.claude import ClaudeAgent

    agent = ClaudeAgent()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    img = tmp_path / "test.png"
    img.write_bytes(b"PNG")

    result = agent._augment_prompt_with_images("describe this", [img], cwd=workspace)
    assert "_attachments/test.png" in result
    assert "describe this" in result
    # Image should be copied to workspace
    assert (workspace / "_attachments" / "test.png").exists()


def test_claude_augment_prompt_with_images_with_string_workspace(tmp_path):
    from oh_my_agent.agents.cli.claude import ClaudeAgent

    agent = ClaudeAgent()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    img = tmp_path / "test.png"
    img.write_bytes(b"PNG")

    result = agent._augment_prompt_with_images("describe this", [img], cwd=str(workspace))
    assert "_attachments/test.png" in result
    assert (workspace / "_attachments" / "test.png").exists()


def test_claude_augment_prompt_empty_list():
    from oh_my_agent.agents.cli.claude import ClaudeAgent

    agent = ClaudeAgent()
    result = agent._augment_prompt_with_images("hello", [], cwd=None)
    assert result == "hello"


# ---------------------------------------------------------------------------
# GeminiCLIAgent._augment_prompt_with_images
# ---------------------------------------------------------------------------

def test_gemini_augment_prompt_with_images(tmp_path):
    from oh_my_agent.agents.cli.gemini import GeminiCLIAgent

    agent = GeminiCLIAgent()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    img = tmp_path / "chart.png"
    img.write_bytes(b"PNG")

    result = agent._augment_prompt_with_images("analyze", [img], cwd=workspace)
    assert "_attachments/chart.png" in result
    assert "analyze" in result
    assert (workspace / "_attachments" / "chart.png").exists()


def test_gemini_augment_prompt_with_string_workspace(tmp_path):
    from oh_my_agent.agents.cli.gemini import GeminiCLIAgent

    agent = GeminiCLIAgent()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    img = tmp_path / "chart.png"
    img.write_bytes(b"PNG")

    result = agent._augment_prompt_with_images("analyze", [img], cwd=str(workspace))
    assert "_attachments/chart.png" in result
    assert (workspace / "_attachments" / "chart.png").exists()


# ---------------------------------------------------------------------------
# CodexCLIAgent._build_command with --image flag
# ---------------------------------------------------------------------------

def test_codex_build_command_without_images():
    from oh_my_agent.agents.cli.codex import CodexCLIAgent

    agent = CodexCLIAgent()
    cmd = agent._build_command("hello")
    assert "--image" not in cmd


def test_codex_build_command_with_images():
    from oh_my_agent.agents.cli.codex import CodexCLIAgent

    agent = CodexCLIAgent()
    paths = [Path("/tmp/a.png"), Path("/tmp/b.jpg")]
    cmd = agent._build_command("hello", image_paths=paths)
    assert "--image" in cmd
    idx = cmd.index("--image")
    assert cmd[idx + 1] == "/tmp/a.png,/tmp/b.jpg"


def test_codex_build_resume_command_with_images():
    from oh_my_agent.agents.cli.codex import CodexCLIAgent

    agent = CodexCLIAgent()
    paths = [Path("/tmp/x.png")]
    cmd = agent._build_resume_command("hello", "sess-123", image_paths=paths)
    assert "--image" in cmd
    idx = cmd.index("--image")
    assert cmd[idx + 1] == "/tmp/x.png"


# ---------------------------------------------------------------------------
# AgentRegistry.run dispatches image_paths
# ---------------------------------------------------------------------------

class _ImageAwareAgent(BaseAgent):
    def __init__(self):
        self.last_image_paths = None

    @property
    def name(self):
        return "img-agent"

    async def run(self, prompt, history=None, *, image_paths=None):
        self.last_image_paths = image_paths
        return AgentResponse(text="ok")


class _PlainAgent(BaseAgent):
    """Agent without image_paths support — should still work."""

    @property
    def name(self):
        return "plain"

    async def run(self, prompt, history=None):
        return AgentResponse(text="ok")


@pytest.mark.asyncio
async def test_registry_passes_image_paths():
    agent = _ImageAwareAgent()
    registry = AgentRegistry([agent])
    paths = [Path("/tmp/img.png")]
    await registry.run("hello", image_paths=paths)
    assert agent.last_image_paths == paths


@pytest.mark.asyncio
async def test_registry_omits_image_paths_for_unsupported_agent():
    agent = _PlainAgent()
    registry = AgentRegistry([agent])
    _, resp = await registry.run("hello", image_paths=[Path("/tmp/img.png")])
    assert resp.text == "ok"


@pytest.mark.asyncio
async def test_registry_force_agent_passes_image_paths():
    agent = _ImageAwareAgent()
    registry = AgentRegistry([agent])
    paths = [Path("/tmp/img.png")]
    await registry.run("hello", force_agent="img-agent", image_paths=paths)
    assert agent.last_image_paths == paths


# ---------------------------------------------------------------------------
# GatewayManager.handle_message with image-only messages
# ---------------------------------------------------------------------------

class _SimpleAgent(BaseAgent):
    def __init__(self):
        self.last_prompt = None

    @property
    def name(self):
        return "simple"

    async def run(self, prompt, history=None, *, thread_id=None, workspace_override=None, image_paths=None):
        self.last_prompt = prompt
        return AgentResponse(text="I see an image")


def _make_channel():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="thread-1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    return channel


@pytest.mark.asyncio
async def test_handle_message_image_only_injects_default_prompt(tmp_path):
    from oh_my_agent.gateway.manager import GatewayManager

    agent = _SimpleAgent()
    registry = AgentRegistry([agent])
    channel = _make_channel()

    gm = GatewayManager([(channel, registry)])
    session = gm._get_session(channel, registry)

    img = tmp_path / "photo.png"
    img.write_bytes(b"PNG")

    msg = IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        author="alice",
        content="",  # no text
        attachments=[
            Attachment("photo.png", "image/png", img, "http://x", 100),
        ],
    )

    await gm.handle_message(session, registry, msg)

    # Agent should have received the default prompt
    assert agent.last_prompt is not None
    assert "describe" in agent.last_prompt.lower() or "analyze" in agent.last_prompt.lower()


@pytest.mark.asyncio
async def test_handle_message_with_text_and_image(tmp_path):
    from oh_my_agent.gateway.manager import GatewayManager

    agent = _SimpleAgent()
    registry = AgentRegistry([agent])
    channel = _make_channel()

    gm = GatewayManager([(channel, registry)])
    session = gm._get_session(channel, registry)

    img = tmp_path / "chart.png"
    img.write_bytes(b"PNG")

    msg = IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        author="alice",
        content="What does this chart show?",
        attachments=[
            Attachment("chart.png", "image/png", img, "http://x", 200),
        ],
    )

    await gm.handle_message(session, registry, msg)
    assert "chart" in agent.last_prompt.lower()


# ---------------------------------------------------------------------------
# ChannelSession.append_user with attachments metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_append_user_with_attachments():
    session = ChannelSession(
        platform="discord",
        channel_id="123",
        channel=MagicMock(),
        registry=MagicMock(),
    )
    attachments = [
        Attachment("img.png", "image/png", Path("/tmp/img.png"), "http://x", 100),
    ]
    await session.append_user("t1", "look at this", "alice", attachments=attachments)
    history = await session.get_history("t1")
    assert len(history) == 1
    assert "attachments" in history[0]
    assert history[0]["attachments"][0]["filename"] == "img.png"
    assert history[0]["attachments"][0]["content_type"] == "image/png"


@pytest.mark.asyncio
async def test_append_user_without_attachments_no_key():
    session = ChannelSession(
        platform="discord",
        channel_id="123",
        channel=MagicMock(),
        registry=MagicMock(),
    )
    await session.append_user("t1", "hello", "alice")
    history = await session.get_history("t1")
    assert "attachments" not in history[0]


# ---------------------------------------------------------------------------
# IncomingMessage backwards compatibility
# ---------------------------------------------------------------------------

def test_incoming_message_default_empty_attachments():
    msg = IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id=None,
        author="alice",
        content="hello",
    )
    assert msg.attachments == []
