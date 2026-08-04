from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import Runnable

from app.classify.schemas import L3ClassificationResult, SubjectExtractionResult
from app.core.config import settings

DEFAULT_MODEL = "claude-sonnet-5"


def build_l3_llm(model: str = DEFAULT_MODEL) -> Runnable:
    """Structured-output-bound chat model for L3 zero-shot classification.
    `.invoke(prompt: str) -> L3ClassificationResult`."""
    chat = ChatAnthropic(
        model=model,
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
