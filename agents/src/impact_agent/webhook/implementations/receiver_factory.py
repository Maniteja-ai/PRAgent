"""Create the GitHub receiver from validated settings and process environment secrets."""

import os
from collections.abc import Mapping

from impact_agent.config.validation.webhook import WebhookConfig
from impact_agent.webhook.implementations.github_receiver import GitHubWebhookReceiver


class GitHubWebhookReceiverFactory:
    @staticmethod
    def create(
        config: WebhookConfig,
        environment: Mapping[str, str] | None = None,
    ) -> GitHubWebhookReceiver:
        if not config.enabled:
            return GitHubWebhookReceiver(config, "")
        values = os.environ if environment is None else environment
        secret = values.get(config.secret_env)
        if not secret:
            raise ValueError(f"GitHub webhook secret is missing from {config.secret_env}")
        return GitHubWebhookReceiver(config, secret)
