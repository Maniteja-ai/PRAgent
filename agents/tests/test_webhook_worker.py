import sqlite3
from pathlib import Path
from threading import Event

import pytest

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.domain.models import PullRequestRef
from impact_agent.webhook.implementations.job_worker import WebhookJobWorker
from impact_agent.webhook.implementations.sqlite_job_queue import SQLiteWebhookJobQueue

CONFIG = Path(__file__).parents[1] / "config" / "default"


class SuccessfulPipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[PullRequestRef, str]] = []

    def run(self, reference: PullRequestRef, run_id: str):
        self.calls.append((reference, run_id))
        return None


class FlakyPipeline:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.run_ids: list[str] = []

    def run(self, reference: PullRequestRef, run_id: str):
        self.run_ids.append(run_id)
        if len(self.run_ids) <= self.failures:
            raise RuntimeError("temporary failure")
        return None


class StoppingPipeline:
    def __init__(self, stop_event: Event) -> None:
        self.stop_event = stop_event
        self.calls = 0

    def run(self, reference: PullRequestRef, run_id: str):
        self.calls += 1
        self.stop_event.set()
        return None


def test_worker_claims_runs_and_completes_queued_job(tmp_path):
    queue = SQLiteWebhookJobQueue(tmp_path / "jobs.sqlite")
    pipeline = SuccessfulPipeline()
    reference = PullRequestRef("owner/storefront", 42)
    queue.submit("delivery-1", reference)
    worker = WebhookJobWorker(queue, pipeline, JsonConfigLoader().load(CONFIG).runtime)

    result = worker.run_once()
    idle = worker.run_once()

    assert result.status == "COMPLETED"
    assert result.delivery_id == "delivery-1"
    assert result.run_id
    assert pipeline.calls == [(reference, result.run_id)]
    assert idle.status == "IDLE"


def test_worker_poll_loop_stops_cleanly_after_shutdown_signal(tmp_path):
    queue = SQLiteWebhookJobQueue(tmp_path / "jobs.sqlite")
    queue.submit("delivery-1", PullRequestRef("owner/storefront", 42))
    stop_event = Event()
    pipeline = StoppingPipeline(stop_event)
    worker = WebhookJobWorker(queue, pipeline, JsonConfigLoader().load(CONFIG).runtime)

    worker.run_forever(stop_event)

    assert pipeline.calls == 1


def test_worker_retries_failure_then_completes_with_same_run_id(tmp_path):
    queue = SQLiteWebhookJobQueue(tmp_path / "jobs.sqlite")
    pipeline = FlakyPipeline(failures=1)
    queue.submit("delivery-1", PullRequestRef("owner/storefront", 42))
    worker = WebhookJobWorker(queue, pipeline, JsonConfigLoader().load(CONFIG).runtime)

    failed_attempt = worker.run_once()
    successful_attempt = worker.run_once()

    assert failed_attempt.status == "RETRY_SCHEDULED"
    assert successful_attempt.status == "COMPLETED"
    assert pipeline.run_ids[0] == pipeline.run_ids[1]


def test_worker_marks_job_failed_after_configured_attempt_limit(tmp_path):
    queue = SQLiteWebhookJobQueue(tmp_path / "jobs.sqlite")
    pipeline = FlakyPipeline(failures=10)
    runtime = JsonConfigLoader().load(CONFIG).runtime.model_copy(update={"job_max_attempts": 2})
    queue.submit("delivery-1", PullRequestRef("owner/storefront", 42))
    worker = WebhookJobWorker(queue, pipeline, runtime)

    assert worker.run_once().status == "RETRY_SCHEDULED"
    result = worker.run_once()

    assert result.status == "FAILED"
    assert worker.run_once().status == "IDLE"


def test_expired_lease_can_be_reclaimed_and_old_worker_cannot_complete(tmp_path):
    database = tmp_path / "jobs.sqlite"
    queue = SQLiteWebhookJobQueue(database)
    queue.submit("delivery-1", PullRequestRef("owner/storefront", 42))

    first_claim = queue.claim_next(300)
    assert first_claim is not None
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE webhook_jobs SET lease_until=unixepoch()-1 WHERE delivery_id=?",
            (first_claim.delivery_id,),
        )
        connection.commit()

    recovered_claim = queue.claim_next(300)
    assert recovered_claim is not None
    assert recovered_claim.attempt == 2
    assert recovered_claim.lease_token != first_claim.lease_token
    with pytest.raises(ValueError, match="lease"):
        queue.complete(first_claim.delivery_id, first_claim.lease_token)

    queue.complete(recovered_claim.delivery_id, recovered_claim.lease_token)
    assert queue.claim_next(300) is None


def test_queue_upgrades_legacy_intake_database(tmp_path):
    database = tmp_path / "legacy.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE webhook_jobs("
            "delivery_id TEXT PRIMARY KEY, repository TEXT NOT NULL, pr_number INTEGER NOT NULL, "
            "status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO webhook_jobs(delivery_id,repository,pr_number,status) "
            "VALUES('delivery-old','owner/storefront',42,'QUEUED')"
        )
        connection.commit()

    queue = SQLiteWebhookJobQueue(database)
    claimed = queue.claim_next(300)

    assert claimed is not None
    assert claimed.reference == PullRequestRef("owner/storefront", 42)
    assert claimed.run_id == "delivery-old"
