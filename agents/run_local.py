"""Run PRAgent's local webhook API and its queue worker for a live demo."""

import os
from pathlib import Path
from threading import Event, Thread

import uvicorn

from impact_agent.dependencies.agent_bootstrap import AgentBootstrap

AGENT_DIRECTORY = Path(__file__).resolve().parent
CONFIG_DIRECTORY = AGENT_DIRECTORY / "config" / "default"


def main() -> None:
    application = AgentBootstrap.create(
        CONFIG_DIRECTORY,
        AGENT_DIRECTORY,
        environment=os.environ,
    )
    stop_event = Event()
    worker = Thread(
        target=application.run_worker,
        args=(stop_event,),
        name="pragent-webhook-worker",
        daemon=True,
    )

    @application.webhook_application.on_event("startup")
    async def start_worker() -> None:
        worker.start()

    @application.webhook_application.on_event("shutdown")
    async def stop_worker() -> None:
        stop_event.set()
        worker.join(timeout=10)
        application.close()

    try:
        uvicorn.run(
            application.webhook_application,
            host="127.0.0.1",
            port=int(os.environ.get("PRAGENT_PORT", "8000")),
            log_level="info",
        )
    finally:
        stop_event.set()
        if worker.is_alive():
            worker.join(timeout=10)
        application.close()


if __name__ == "__main__":
    main()
