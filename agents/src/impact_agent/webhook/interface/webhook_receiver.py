"""Port for validating and translating GitHub webhook deliveries."""

from collections.abc import Mapping
from typing import Protocol

from impact_agent.domain.models import WebhookAcknowledgement


class WebhookReceiver(Protocol):
    def receive(self, headers: Mapping[str, str], body: bytes) -> WebhookAcknowledgement: ...
