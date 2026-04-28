"""External push notification layer.

Distinct from the internal ``runtime.notifications.NotificationManager``,
which fans out owner-action-required pings inside Discord. This layer
sends notifications to external apps (Bark on iOS, future ntfy/wecom)
to break through OS-level focus modes when @-mentions or automation
terminals need urgent owner attention.
"""

from oh_my_agent.push_notifications.bark import BarkPushProvider
from oh_my_agent.push_notifications.base import (
    NoopPushProvider,
    PushDispatcher,
    PushKind,
    PushLevel,
    PushNotificationEvent,
    PushNotificationProvider,
    PushSettings,
)

__all__ = [
    "BarkPushProvider",
    "NoopPushProvider",
    "PushDispatcher",
    "PushKind",
    "PushLevel",
    "PushNotificationEvent",
    "PushNotificationProvider",
    "PushSettings",
]
