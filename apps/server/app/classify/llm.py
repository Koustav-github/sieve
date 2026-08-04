from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import Runnable
from langchain_groq import ChatGroq

from app.classify.schemas import L3ClassificationResult, SubjectExtractionResult
from app.core.config import settings

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def build_l3_llm(model: str | None = None, provider: str | None = None) -> Runnable:
    """Structured-output-bound chat model for L3 zero-shot classification.
    `.invoke(prompt: str) -> L3ClassificationResult`.

    `provider` selects the LLM vendor (`settings.classification_llm_provider`
    by default, one of "anthropic"/"groq") - Groq is an optional lower-cost
    alternative for this cascade layer; subject extraction always stays on
    Anthropic (untested and unspecified on Groq, and not part of the design
    this option merges in)."""
    provider = provider or settings.classification_llm_provider
    if provider == "groq":
        chat = ChatGroq(
            model=model or DEFAULT_GROQ_MODEL,
            api_key=settings.groq_api_key,
            temperature=0,
            timeout=30,
            max_retries=2,
        )
        return chat.with_structured_output(L3ClassificationResult)
    if provider != "anthropic":
        raise ValueError(f"Unknown classification_llm_provider: {provider!r}")
    chat = ChatAnthropic(
        model=model or DEFAULT_MODEL,
        api_key=settings.anthropic_api_key,
        temperature=0,
        timeout=30,
        max_retries=2,
    )
    return chat.with_structured_output(L3ClassificationResult)


def build_subject_extraction_llm(model: str = DEFAULT_MODEL) -> Runnable:
    """Structured-output-bound chat model for subject extraction.
    `.invoke(prompt: str) -> SubjectExtractionResult`."""
    chat = ChatAnthropic(
        model=model,
        api_key=settings.anthropic_api_key,
        temperature=0,
        timeout=30,
        max_retries=2,
    )
    return chat.with_structured_output(SubjectExtractionResult)
