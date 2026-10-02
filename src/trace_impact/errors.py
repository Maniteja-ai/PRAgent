"""Safe boundary errors: messages must not contain credentials or document content."""


class IngestionError(Exception):
    code = "INGESTION_ERROR"


class SourceReadError(IngestionError):
    code = "SOURCE_READ_FAILED"


class ExtractionError(IngestionError):
    code = "EXTRACTION_FAILED"


class ArtifactError(IngestionError):
    code = "ARTIFACT_INVALID"


class RunBusyError(IngestionError):
    code = "RUN_BUSY"


class ConfigurationError(IngestionError, ValueError):
    code = "CONFIGURATION_ERROR"


class ProviderError(IngestionError):
    code = "PROVIDER_REQUEST_FAILED"
