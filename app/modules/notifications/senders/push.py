"""Push notification sender — thin wrapper that delegates to a pluggable provider.

Default provider is FCM. To swap, pass a different BasePushProvider instance::

    sender = PushSender(provider=SomeOtherProvider())
"""

from app.modules.notifications.enums import NotificationStatus
from app.modules.notifications.senders.providers.base import BasePushProvider


class PushSender:
    """Sends push notifications via the configured provider (default: FCM)."""

    def __init__(self, provider: BasePushProvider | None = None) -> None:
        if provider is None:
            from app.modules.notifications.senders.providers.fcm import FCMProvider

            provider = FCMProvider()
        self._provider = provider

    async def send(
        self,
        *,
        device_token: str,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> tuple[NotificationStatus, str | None, str | None]:
        """Send a push notification to a single device.

        Returns:
            (status, error_message, external_id)
        """
        return await self._provider.send(
            device_token=device_token,
            title=title,
            body=body,
            data=data,
        )
