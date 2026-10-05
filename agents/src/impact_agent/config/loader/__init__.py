"""Configuration loading interfaces and implementations."""

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.config.loader.interface.config_loader import ConfigLoader

__all__ = ["ConfigLoader", "JsonConfigLoader"]
