"""Authenticate and translate GitHub pull request webhook payloads."""

import hashlib
import hmac
import json
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from impact_agent.config.validation.webhook import WebhookConfig
from impact_agent.domain.models import PullRequestRef, WebhookAcknowledgement, WebhookRejection


class _PullRequestPayload(BaseModel):
    number: int = Field(gt=0)


class _RepositoryPayload(BaseModel):
    full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=200)


class _GitHubPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = Field(min_length=1, max_length=40)
    pull_request: _PullRequestPayload
    repository: _RepositoryPayload


class GitHubWebhookReceiver:
    """Verify GitHub's SHA-256 HMAC and accept only configured PR actions."""

    def __init__(self, config: WebhookConfig, secret: str) -> None:
        if config.enabled and not secret:
            raise ValueError("GitHub webhook secret must be configured")
        self._config = config
        self._secret = secret.encode("utf-8")

    def receive(self, headers: Mapping[str, str], body: bytes) -> WebhookAcknowledgement:
        normalized_headers = {key.casefold(): value for key, value in headers.items()}
        delivery_id = normalized_headers.get("x-github-delivery", "")

        if not self._config.enabled:
            return self._reject(delivery_id, "WEBHOOK_DISABLED")
        if len(body) > self._config.max_body_bytes:
            return self._reject(delivery_id, "BODY_TOO_LARGE")

        signature = normalized_headers.get("x-hub-signature-256", "")
        event = normalized_headers.get("x-github-event", "")
        if not delivery_id or not signature or not event:
            return self._reject(delivery_id, "MISSING_HEADERS")
        if not self._valid_signature(body, signature):
            return self._reject(delivery_id, "INVALID_SIGNATURE")
        if event != self._config.accepted_event:
            return self._reject(delivery_id, "UNSUPPORTED_EVENT")

        try:
            decoded = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._reject(delivery_id, "INVALID_JSON")
        try:
            payload = _GitHubPayload.model_validate(decoded)
        except ValidationError:
            return self._reject(delivery_id, "INVALID_PULL_REQUEST")
        if payload.action not in self._config.accepted_actions:
            return self._reject(delivery_id, "UNSUPPORTED_ACTION")

        reference = PullRequestRef(
            repository=payload.repository.full_name,
            number=payload.pull_request.number,
        )
        return WebhookAcknowledgement(accepted=True, delivery_id=delivery_id, reference=reference)

    def _valid_signature(self, body: bytes, signature: str) -> bool:
        try:
            provided = signature.encode("ascii")
        except UnicodeEncodeError:
            return False
        if not signature.startswith("sha256="):
            return False
        expected = ("sha256=" + hmac.new(self._secret, body, hashlib.sha256).hexdigest()).encode(
            "ascii"
        )
        return hmac.compare_digest(expected, provided)

    @staticmethod
    def _reject(delivery_id: str, reason: WebhookRejection) -> WebhookAcknowledgement:
        return WebhookAcknowledgement(accepted=False, delivery_id=delivery_id, reason=reason)
