"""JSON events go to stderr; CLI results remain machine-readable on stdout."""

import json
import logging


class JsonEventSink:
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def emit(self, event: str, **fields: str | int | float) -> None:
        # Callers supply IDs, counts and safe error codes, never raw prompts/exceptions.
        self.logger.info(json.dumps({"event": event, **fields}, sort_keys=True))
