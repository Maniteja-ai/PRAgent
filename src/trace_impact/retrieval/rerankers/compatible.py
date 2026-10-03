"""Explicit OpenAI-compatible Chat Completions transport for cheaper model choices."""

from contextlib import ExitStack

from trace_impact.retrieval.rerankers.credentials import resolve_api_key
from trace_impact.retrieval.rerankers.llm import PROMPT, LLMReranker, provider_schema
from trace_impact.shared.errors import ConfigurationError
from trace_impact.shared.rate_limit import RequestPacer


def build_compatible_reranker(options):
    api_key = resolve_api_key(options.api_key_env)
    try:
        import httpx
        from langchain_openai import ChatOpenAI
        from openai import OpenAIError
    except ImportError:
        raise ConfigurationError(
            "Install the 'openai' optional dependency for openai_compatible reranking"
        ) from None

    with ExitStack() as resources:
        client = resources.enter_context(
            httpx.Client(timeout=options.timeout_seconds, follow_redirects=False)
        )
        llm = ChatOpenAI(
            model=options.model,
            api_key=api_key,
            base_url=options.base_url,
            timeout=options.timeout_seconds,
            max_retries=options.max_retries,
            max_tokens=options.max_output_tokens,
            http_client=client,
            use_responses_api=False,
        )
        structured = llm.with_structured_output(
            provider_schema(),
            method=options.structured_output,
            include_raw=True,
            **({"strict": True} if options.structured_output == "json_schema" else {}),
        )
        return LLMReranker(
            structured,
            options,
            before_request=RequestPacer(options.requests_per_minute).wait,
            close=resources.pop_all().close,
            request_errors=(OpenAIError, httpx.HTTPError),
            prompt=PROMPT
            + '\nReturn JSON with this structure: {"items": [{"id": "supplied ID", "grade": 0, "quote": ""}]}',
        )
