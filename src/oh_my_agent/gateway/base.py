from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Attachment:
    """A file attachment downloaded to the local filesystem."""

    filename: str
    content_type: str
    local_path: Path
    original_url: str
    size_bytes: int

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")


@dataclass
class OutgoingAttachment:
    """A file attachment to upload to the chat platform."""

    filename: str
    content_type: str
    local_path: Path
    caption: str | None = None


@dataclass
class IncomingMessage:
    """Platform-agnostic representation of a received message."""

    platform: str           # "discord" (other platforms post-1.0)
    channel_id: str         # Platform-specific channel identifier
    thread_id: str | None   # Existing thread ID; None means create a new thread
    author: str             # Display name of the sender
    content: str            # Message text
    author_id: str | None = None  # Stable sender identifier (platform user ID)
    raw: Any = field(default=None, repr=False)  # Original platform object
    # When set, bypass fallback and run only this named agent
    preferred_agent: str | None = None
    # Internal/system-generated message (e.g. scheduler); bypasses owner gate.
    system: bool = False
    # File attachments (e.g. images) downloaded to local temp paths.
    attachments: list[Attachment] = field(default_factory=list)
    # Platform message id this message is a reply to (e.g. Discord reply anchor).
    # Used by the gateway to promote replies to automation posts into follow-up threads.
    reply_to_message_id: str | None = None


MessageHandler = Callable[[IncomingMessage], Awaitable[None]]

# Slash command handlers — set by GatewayManager, consumed by platform channels
SlashResetHandler = Callable[[str, str, str], Awaitable[str]]           # (platform, channel_id, thread_id) → confirmation
SlashAgentHandler = Callable[[str, str], Awaitable[str]]                # (platform, channel_id) → agent list info
SlashSearchHandler = Callable[[str, int], Awaitable[list[dict]]]        # (query, limit) → results


@dataclass
class ActionDescriptor:
    """A single action button in an interactive message."""

    id: str
    label: str
    style: str = "secondary"   # primary | secondary | danger | success
    disabled: bool = False


@dataclass
class InteractivePrompt:
    """Platform-neutral interactive message with action buttons.

    Used for HITL prompts, task approvals, and other owner interactions.
    Platform adapters translate this into native widgets (Discord
    buttons, Slack Block Kit, etc.).
    """

    text: str
    actions: list[ActionDescriptor] = field(default_factory=list)
    idempotency_key: str | None = None
    entity_kind: str | None = None
    entity_id: str | None = None    # task_id, prompt_id, etc.


class TaskDecisionSurface(Protocol):
    def supports_buttons(self) -> bool: ...

    async def send_task_draft(
        self,
        *,
        thread_id: str,
        draft_text: str,
        task_id: str,
        nonce: str,
        actions: list[str],
    ) -> str | None: ...

    def parse_decision_event(self, raw: Any) -> Any | None: ...


class BaseChannel(ABC):
    """Platform adapter: bridges a chat platform with the GatewayManager."""

    @property
    @abstractmethod
    def platform(self) -> str: ...

    @property
    @abstractmethod
    def channel_id(self) -> str: ...

    @abstractmethod
    async def start(self, on_message: MessageHandler) -> None:
        """Connect to the platform and start listening. Blocks until stopped."""
        ...

    @abstractmethod
    async def create_thread(self, msg: IncomingMessage, name: str) -> str:
        """Create a new thread from *msg* and return its platform thread_id."""
        ...

    async def create_followup_thread(
        self,
        anchor_message_id: str,
        name: str,
    ) -> str | None:
        """Create a thread attached to an existing channel message.

        Used to spawn a follow-up thread rooted at a prior automation post.
        Default implementation returns ``None`` — platforms that support
        message-anchored threads (e.g. Discord) should override.
        """
        del anchor_message_id, name
        return None

    @abstractmethod
    async def send(self, thread_id: str, text: str) -> str | None:
        """Send *text* to the given thread and optionally return message id."""
        ...

    # -- lifecycle --------------------------------------------------------

    async def stop(self) -> None:
        """Shut down the channel connection gracefully. No-op by default."""

    # -- message editing --------------------------------------------------

    async def edit_message(
        self,
        thread_id: str,
        message_id: str,
        text: str,
    ) -> None:
        """Edit an existing message's text. No-op by default."""
        del thread_id, message_id, text

    # -- interactive messages ---------------------------------------------

    async def send_interactive(
        self,
        thread_id: str,
        prompt: InteractivePrompt,
    ) -> str | None:
        """Send an interactive message with action buttons.

        Default implementation sends the prompt text as a plain message
        (buttons are silently dropped).
        """
        return await self.send(thread_id, prompt.text)

    async def update_interactive(
        self,
        thread_id: str,
        message_id: str,
        prompt: InteractivePrompt,
    ) -> None:
        """Update an existing interactive message (e.g. disable buttons).

        Default implementation falls back to ``edit_message``.
        """
        await self.edit_message(thread_id, message_id, prompt.text)

    # -- platform helpers -------------------------------------------------

    def render_user_mention(self, user_id: str) -> str:
        """Render a user mention for the current platform."""
        return f"`{user_id}`"

    async def send_dm(self, user_id: str, text: str) -> str | None:
        """Best-effort DM delivery. Default implementation is unsupported."""
        del user_id, text
        return None

    async def upsert_status_message(
        self,
        thread_id: str,
        text: str,
        *,
        message_id: str | None = None,
    ) -> str | None:
        """Create or update a platform status message. Default fallback sends a new message."""
        del message_id
        return await self.send(thread_id, text)

    async def send_attachment(
        self,
        thread_id: str,
        attachment: OutgoingAttachment,
    ) -> str | None:
        note = attachment.caption or f"Attachment available: {attachment.filename}"
        return await self.send(thread_id, note)

    async def send_attachments(
        self,
        thread_id: str,
        attachments: list[OutgoingAttachment],
        *,
        text: str | None = None,
    ) -> list[str]:
        message_ids: list[str] = []
        if text:
            msg_id = await self.send(thread_id, text)
            if msg_id:
                message_ids.append(msg_id)
        for attachment in attachments:
            msg_id = await self.send_attachment(thread_id, attachment)
            if msg_id:
                message_ids.append(msg_id)
        return message_ids

    @asynccontextmanager
    async def typing(self, thread_id: str) -> AsyncIterator[None]:
        """Show a typing indicator while the body executes. Optional — default is no-op."""
        yield

    def supports_buttons(self) -> bool:
        return False

    async def send_task_draft(
        self,
        *,
        thread_id: str,
        draft_text: str,
        task_id: str,
        nonce: str,
        actions: list[str],
    ) -> str | None:
        del task_id, nonce, actions
        await self.send(thread_id, draft_text)
        return None

    def parse_decision_event(self, raw: Any) -> Any | None:
        del raw
        return None
