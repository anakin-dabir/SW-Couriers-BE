"""Abstract base for push notification providers.

Swap implementations by passing a different provider to PushSender.
All providers must return the same (status, error_message, external_id) tuple.
"""

from abc import ABC, abstractmethod

from app.modules.notifications.enums import NotificationStatus


class BasePushProvider(ABC):
    """Interface that every push provider must implement."""

    @abstractmethod
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
        ...
