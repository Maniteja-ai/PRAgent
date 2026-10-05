"""Validation for ``browser.json``."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from impact_agent.config.validation.common import StrictSettings


class BrowserConfig(StrictSettings):
    provider: Literal["playwright", "disabled"] = "playwright"
    start_urls: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=30, gt=0, le=180)
    max_pages: int = Field(default=5, ge=1, le=20)
    max_page_characters: int = Field(default=20_000, ge=500, le=100_000)

    @model_validator(mode="after")
    def validate_urls(self) -> "BrowserConfig":
        normalized_hosts = {host.lower() for host in self.allowed_hosts}
        for url in self.start_urls:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("Browser start_urls must use HTTPS and include a hostname")
            if parsed.hostname.lower() not in normalized_hosts:
                raise ValueError("Every browser start URL host must appear in allowed_hosts")
        if len(self.start_urls) > self.max_pages:
            raise ValueError("Browser start_urls cannot exceed max_pages")
        return self
