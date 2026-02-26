from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IncomingMessage:
    """Platform-agnostic representation of a received message."""

    platform: str           # "discord" | "slack" | "telegram"
    channel_id: str         # Platform-specific channel identifier
    thread_id: str | None   # Existing thread ID; None means create a new thread
    author: str             # Display name of the sender
    content: str            # Message text
    raw: Any = field(default=None, repr=False)  # Original platform object


MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


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

    @abstractmethod
    async def send(self, thread_id: str, text: str) -> None:
        """Send *text* to the given thread."""
        ...

    @asynccontextmanager
    async def typing(self, thread_id: str) -> AsyncIterator[None]:
        """Show a typing indicator while the body executes. Optional — default is no-op."""
        yield
