"""Optional, content-safe LangSmith tracing that never controls workflow execution."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from trace_coordinator.config import DisabledObservability, LangSmithObservability
from trace_coordinator.ledger import canonical


@dataclass
class TraceSession:
    """One best-effort trace attachment for one immutable coordinator run."""

    annotation: dict[str, Any] | None = None
    callback: Any = None
    client: Any = None
    flush_timeout_seconds: float = 0

    def graph_options(self) -> dict[str, Any]:
        return {"callbacks": [self.callback]} if self.callback is not None else {}

    def finish(self) -> dict[str, Any] | None:
        if self.annotation is None:
            return None
        result = dict(self.annotation)
        if self.callback is not None:
            latest = getattr(self.callback, "latest_run", None)
            if latest is not None:
                result["trace_id"] = str(latest.id)
                result["status"] = "SUBMITTED"
            else:
                result["status"] = "UNAVAILABLE"
                result["reason"] = "LangGraph did not create a root trace"
        if self.client is not None:
            try:
                self.client.flush(timeout=self.flush_timeout_seconds)
            except Exception:
                # Observability is intentionally fail-open. Provider details can contain secrets.
                result["delivery"] = "BEST_EFFORT"
            else:
                result["delivery"] = "FLUSH_REQUESTED"
            try:
                self.client.close(timeout=0)
            except Exception:
                pass
        return result


class CoordinatorObservability:
    """Builds per-run callbacks and persists only safe trace coordinates in the local ledger."""

    def __init__(self, config: DisabledObservability | LangSmithObservability | None = None):
        self.config = config or DisabledObservability()

    @property
    def fingerprint(self) -> dict[str, Any]:
        return self.config.model_dump(mode="json")

    def start(
        self,
        *,
        run_id: str,
        request,
        workflow_version: str,
        execution_metadata: dict[str, str] | None = None,
    ) -> TraceSession:
        if self.config.provider == "disabled":
            return TraceSession()
        base = {
            "provider": "langsmith",
            "status": "INITIALIZED",
            "project": self.config.project,
            "dashboard_url": self.config.dashboard_url,
            "trace_id": None,
            "content_captured": False,
        }
        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            return TraceSession(
                annotation={
                    **base,
                    "status": "UNAVAILABLE",
                    "reason": f"Environment variable {self.config.api_key_env} is not set",
                }
            )
        client = None
        try:
            # The SDK otherwise creates non-daemon delivery workers that can hold a CLI or
            # short-lived webhook worker open during a provider outage. We perform a bounded
            # flush below; anything still pending must never delay coordinator shutdown.
            os.environ.setdefault("LANGSMITH_USE_DAEMON", "true")
            from langchain_core.tracers.langchain import LangChainTracer
            from langsmith import Client
            from urllib3.util import Retry

            endpoint = os.getenv(self.config.endpoint_env) if self.config.endpoint_env else None
            workspace_id = os.getenv(self.config.workspace_id_env) if self.config.workspace_id_env else None
            client = Client(
                api_key=api_key,
                api_url=endpoint,
                workspace_id=workspace_id,
                auto_batch_tracing=True,
                info={},
                retry_config=Retry(total=0, connect=0, read=0, status=0, redirect=0),
                timeout_ms=int(self.config.request_timeout_seconds * 1000),
                hide_inputs=True,
                hide_outputs=True,
                hide_metadata=False,
                omit_traced_runtime_info=True,
                tracing_sampling_rate=self.config.sampling_rate,
            )
            metadata = {
                "run_id": run_id,
                "project_id": request.project_id,
                "repository": request.repository,
                "pull_request": str(request.pull_request),
                "workflow_version": workflow_version,
                **(execution_metadata or {}),
            }
            tags = ["pr-impact-analysis", "langgraph", f"repository:{request.repository}"]
            tags.extend(f"{key}:{value}" for key, value in sorted((execution_metadata or {}).items()))
            callback = LangChainTracer(
                project_name=self.config.project,
                client=client,
                tags=tags,
                metadata=metadata,
            )
            callback.raise_error = False
            return TraceSession(
                annotation=base,
                callback=callback,
                client=client,
                flush_timeout_seconds=self.config.flush_timeout_seconds,
            )
        except Exception:
            if client is not None:
                try:
                    client.close(timeout=0)
                except Exception:
                    pass
            return TraceSession(
                annotation={
                    **base,
                    "status": "UNAVAILABLE",
                    "reason": "LangSmith tracing could not be initialized",
                }
            )

    @staticmethod
    def attach(ledger, run_id: str, result: dict[str, Any], session: TraceSession):
        previous = ledger.latest_event(run_id, "OBSERVABILITY")
        annotation = session.finish()
        if annotation is None:
            return result
        if (
            previous
            and annotation.get("trace_id") is None
            and annotation.get("project") == json.loads(previous["detail"]).get("project")
        ):
            annotation = json.loads(previous["detail"])
        ledger.event_once(run_id, "OBSERVABILITY", canonical(annotation))
        enriched = dict(result)
        enriched["observability"] = annotation
        enriched["observability_events"] = [
            event for event in ledger.events(run_id) if event["kind"] == "OBSERVABILITY"
        ]
        return enriched
