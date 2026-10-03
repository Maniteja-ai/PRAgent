"""Small contracts used by GitHub webhook processing."""

from typing import Protocol, TypedDict

from trace_coordinator.domain.contracts import JsonObject


class WebhookJob(TypedDict):
    delivery_id: str
    payload_sha256: str
    repository: str
    pull_request: int
    base_sha: str
    head_sha: str
    installation_id: int
    action: str
    run_id: str


class ClaimedJob(TypedDict):
    delivery_id: str
    repository: str
    pull_request: int
    base_sha: str
    head_sha: str
    installation_id: int
    action: str
    run_id: str


class AccessTokenProvider(Protocol):
    def token(self, installation_id: int) -> str: ...


class PullRequestCommentPublisher(Protocol):
    def publish(self, job: ClaimedJob, body: str) -> JsonObject: ...
