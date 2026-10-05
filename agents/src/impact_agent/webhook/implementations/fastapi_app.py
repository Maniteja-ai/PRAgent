"""HTTP endpoint for authenticated GitHub webhook deliveries."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from impact_agent.config.validation.webhook import WebhookConfig
from impact_agent.webhook.interface.webhook_job_handler import (
    WebhookDeliveryConflict,
    WebhookJobHandler,
)
from impact_agent.webhook.interface.webhook_receiver import WebhookReceiver


def create_webhook_app(
    config: WebhookConfig,
    receiver: WebhookReceiver,
    job_handler: WebhookJobHandler,
) -> FastAPI:
    """Build a small API app with receiver and queue dependencies injected."""

    app = FastAPI(title="Testsigma PR Impact Agent Webhook", docs_url=None, redoc_url=None)

    @app.post("/webhooks/github", status_code=202)
    async def receive_github_webhook(request: Request) -> JSONResponse:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError as error:
                raise HTTPException(status_code=400, detail="Invalid Content-Length") from error
            if declared_size < 0 or declared_size > config.max_body_bytes:
                raise HTTPException(
                    status_code=413, detail="Webhook payload exceeds configured size"
                )

        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > config.max_body_bytes:
                raise HTTPException(
                    status_code=413, detail="Webhook payload exceeds configured size"
                )
            body.extend(chunk)
        acknowledgement = receiver.receive(request.headers, bytes(body))
        if not acknowledgement.accepted:
            status = {
                "BODY_TOO_LARGE": 413,
                "MISSING_HEADERS": 400,
                "INVALID_SIGNATURE": 401,
            }.get(acknowledgement.reason or "", 202)
            if status in {400, 401, 413}:
                raise HTTPException(status_code=status, detail=acknowledgement.reason)
            return JSONResponse(
                status_code=202,
                content={"status": "IGNORED", "reason": acknowledgement.reason},
            )

        if acknowledgement.reference is None:
            raise HTTPException(
                status_code=400, detail="Accepted event has no pull request reference"
            )
        try:
            job = job_handler.submit(acknowledgement.delivery_id, acknowledgement.reference)
        except WebhookDeliveryConflict as error:
            raise HTTPException(status_code=409, detail="GitHub delivery ID was reused") from error
        return JSONResponse(
            status_code=202,
            content={"status": job.status, "delivery_id": acknowledgement.delivery_id},
        )

    return app
