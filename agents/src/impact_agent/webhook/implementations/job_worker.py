"""Run queued pull request analyses outside the webhook request thread."""

import logging
from dataclasses import dataclass
from threading import Event
from typing import Literal

from impact_agent.config.validation.runtime import RuntimeConfig
from impact_agent.domain.models import WorkerExecution
from impact_agent.pipeline.interface.agent_pipeline import AgentPipeline
from impact_agent.pull_request.interface.report_commenter import ReportCommenter
from impact_agent.webhook.interface.webhook_job_handler import WebhookJobQueue

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WebhookJobWorker:
    """Claim one job, execute the injected pipeline, then acknowledge or retry it."""

    queue: WebhookJobQueue
    pipeline: AgentPipeline
    runtime: RuntimeConfig
    report_commenter: ReportCommenter | None = None

    def run_once(self) -> WorkerExecution:
        job = self.queue.claim_next(self.runtime.job_lease_seconds)
        if job is None:
            return WorkerExecution("IDLE")

        try:
            report = self.pipeline.run(job.reference, job.run_id)
            if self.report_commenter is not None:
                self.report_commenter.publish(job.reference, report)
        except Exception as error:
            outcome = self.queue.fail(
                job.delivery_id,
                job.lease_token,
                type(error).__name__,
                self.runtime.job_max_attempts,
            )
            LOGGER.exception(
                "PR analysis job failed; delivery_id=%s run_id=%s attempt=%d outcome=%s",
                job.delivery_id,
                job.run_id,
                job.attempt,
                outcome,
            )
            worker_status: Literal["RETRY_SCHEDULED", "FAILED"] = (
                "RETRY_SCHEDULED" if outcome == "REQUEUED" else "FAILED"
            )
            return WorkerExecution(worker_status, job.delivery_id, job.run_id)

        self.queue.complete(job.delivery_id, job.lease_token)
        return WorkerExecution("COMPLETED", job.delivery_id, job.run_id)

    def run_forever(self, stop_event: Event) -> None:
        """Poll the queue until the host asks this worker to shut down."""
        while not stop_event.is_set():
            try:
                result = self.run_once()
            except Exception:
                LOGGER.exception("Webhook worker iteration failed")
                stop_event.wait(self.runtime.job_poll_seconds)
                continue
            if result.status == "IDLE":
                stop_event.wait(self.runtime.job_poll_seconds)
