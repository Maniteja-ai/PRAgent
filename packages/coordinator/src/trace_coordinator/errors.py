"""Public, safe failure categories; raw provider messages are never persisted."""

from enum import StrEnum


class FailureCode(StrEnum):
    UNSPECIFIED = "UNSPECIFIED"
    MODEL_RESPONSE_INVALID = "MODEL_RESPONSE_INVALID"
    MODEL_CONTEXT_LIMIT = "MODEL_CONTEXT_LIMIT"
    PROVIDER_TRANSIENT = "PROVIDER_TRANSIENT"
    PROVIDER_AUTH = "PROVIDER_AUTH"
    PROVIDER_INVALID_REQUEST = "PROVIDER_INVALID_REQUEST"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    GUARDRAIL_BLOCKED = "GUARDRAIL_BLOCKED"
    BROWSER_ACTION_DENIED = "BROWSER_ACTION_DENIED"
    BROWSER_STALE_SNAPSHOT = "BROWSER_STALE_SNAPSHOT"


class LimitReached(RuntimeError):
    pass


class UncertainExecution(RuntimeError):
    pass


class RunMismatch(ValueError):
    pass


class ToolFailure(RuntimeError):
    def __init__(self, message="Tool failed", *, retryable=False, code=FailureCode.UNSPECIFIED):
        super().__init__(message)
        self.retryable = retryable
        self.code = FailureCode(code)
