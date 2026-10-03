"""Evaluate retrieval results without modifying the pipeline that produced them."""

from trace_impact.evals.config import EvaluationConfig, Prediction
from trace_impact.evals.scoring.retrieval import CaseInput, PipelineAdapter, evaluate

__all__ = ["CaseInput", "EvaluationConfig", "PipelineAdapter", "Prediction", "evaluate"]
