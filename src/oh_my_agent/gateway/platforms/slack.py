from __future__ import annotations

from oh_my_agent.gateway.base import BaseChannel, IncomingMessage, MessageHandler


class SlackChannel(BaseChannel):
    """Slack platform adapter — not yet implemented."""

    def __init__(self, token: str, channel_id: str) -> None:
        self._token = token
        self._channel_id = channel_id

    @property
    def platform(self) -> str:
        return "slack"

    @property
    def channel_id(self) -> str:
        return self._channel_id

    async def start(self, on_message: MessageHandler) -> None:
        raise NotImplementedError("Slack adapter is not yet implemented")

    async def create_thread(self, msg: IncomingMessage, name: str) -> str:
        raise NotImplementedError

    async def send(self, thread_id: str, text: str) -> str | None:
        raise NotImplementedError
