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
