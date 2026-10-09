from abc import ABC, abstractmethod
from typing import Dict, Any


class NotificationProvider(ABC):
    """
    Abstract Base Class for StockPulse Notification Providers.
    Allows seamlessly extending the system with new delivery channels (Slack, SMS, Webhooks)
    without modifying core monitoring or ML logic.
    """

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Returns the channel identifier, e.g. 'popup', 'dashboard'."""
        pass

    @abstractmethod
    def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Delivers the notification payload.
        Returns a dict:
        {
            "success": bool,
            "channel": str,
            "message_id": Optional[str],
            "error": Optional[str],
            "details": Optional[Dict[str, Any]]
        }
        """
        raise NotImplementedError
