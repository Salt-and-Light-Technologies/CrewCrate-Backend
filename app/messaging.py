from typing import Protocol


class MessagingProvider(Protocol):
    async def send_sms(self, *, recipient: str, body: str, idempotency_key: str) -> str: ...


class MessagingUnavailable(RuntimeError):
    pass


class DisabledMessagingProvider:
    async def send_sms(self, **kwargs) -> str:
        raise MessagingUnavailable("Messaging provider is not connected; nothing was sent")
