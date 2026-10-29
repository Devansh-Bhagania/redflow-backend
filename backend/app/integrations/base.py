from abc import ABC, abstractmethod


class MessagingProvider(ABC):
    """Common interface every outbound messaging channel implements
    (WhatsApp today; Email/SMS land in later phases with their own subclass)."""

    @abstractmethod
    async def send(self, *, to: str, text: str, credentials: dict) -> str:
        """Send a text message, return the provider's external message id."""
