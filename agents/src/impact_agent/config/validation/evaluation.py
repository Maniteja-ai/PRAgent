"""Validation for ``evaluation.json``."""

from impact_agent.config.validation.common import StrictSettings


class EvaluationConfig(StrictSettings):
    record_stage_results: bool = True
    dataset_path: str = "evaluation/datasets/pr-impact-v1.json"
