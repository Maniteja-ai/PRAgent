"""Gemini transport for the shared, validated LLM relevance grader."""

from trace_impact.retrieval.rerankers.credentials import resolve_api_key
from trace_impact.retrieval.rerankers.llm import Grade as Grade
from trace_impact.retrieval.rerankers.llm import Grades as Grades
from trace_impact.retrieval.rerankers.llm import LLMReranker, provider_schema
from trace_impact.shared.rate_limit import RequestPacer

SCORE_KIND = "gemini-relevance-v1"


class GeminiReranker(LLMReranker):
    def __init__(self, model, options, **kwargs):
        super().__init__(model, options, score_kind=SCORE_KIND, finish_reasons=("STOP",), **kwargs)


def build_gemini_reranker(settings, options):
    import httpx
    from google.genai import errors
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_google_genai._common import GoogleGenerativeAIError

    api_key = resolve_api_key(options.api_key_env, settings.gemini_api_key)
    optional = {}
    if options.base_url is not None:
        optional["base_url"] = options.base_url
    if options.thinking_level is not None:
        optional["thinking_level"] = options.thinking_level
    llm = ChatGoogleGenerativeAI(
        model=options.model,
        api_key=api_key,
        vertexai=False,
        timeout=options.timeout_seconds,
        max_retries=options.max_retries + 1,
        max_output_tokens=options.max_output_tokens,
        **optional,
    )
    try:
        return GeminiReranker(
            llm.with_structured_output(provider_schema(), method="json_schema", include_raw=True),
            options,
            before_request=RequestPacer(options.requests_per_minute).wait,
            close=llm.client.close,
            request_errors=(errors.APIError, GoogleGenerativeAIError, httpx.HTTPError),
        )
    except Exception:
        llm.client.close()
        raise
