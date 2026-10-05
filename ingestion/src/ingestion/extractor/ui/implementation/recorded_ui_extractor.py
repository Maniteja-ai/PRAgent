from ingestion.beans.decorators import component
from ingestion.config_loader.models import UiInputConfig
from ingestion.domain.models import UiObservation
from ingestion.extractor.ui.interface import UiExtractor


@component(contract=UiExtractor, name="recorded")
class RecordedUiExtractor:
    """Safe placeholder until a live Playwright adapter is configured."""

    def extract(self, config: UiInputConfig) -> tuple[UiObservation, ...]:
        return tuple(
            UiObservation(id=f"page:{index}", url=config.base_url.rstrip("/") + path)
            for index, path in enumerate(config.seed_paths)
        )
