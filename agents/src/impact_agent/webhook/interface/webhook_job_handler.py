"""Port for handing an authenticated pull request event to durable processing."""

from typing import Literal, Protocol

from impact_agent.domain.models import PullRequestRef, WebhookJob, WebhookJobResult


class WebhookDeliveryConflict(ValueError):
    """One delivery ID was already recorded for a different pull request."""


class WebhookJobHandler(Protocol):
    def submit(self, delivery_id: str, reference: PullRequestRef) -> WebhookJobResult: ...


class WebhookJobQueue(WebhookJobHandler, Protocol):
    """Queue operations used by the receiver and background worker."""

    def claim_next(self, lease_seconds: int) -> WebhookJob | None: ...

    def complete(self, delivery_id: str, lease_token: str) -> None: ...

    def fail(
        self, delivery_id: str, lease_token: str, error_type: str, max_attempts: int
    ) -> Literal["REQUEUED", "FAILED"]: ...
