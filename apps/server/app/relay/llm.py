from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import Runnable

from app.core.config import settings
from app.relay.schemas import RelayExtractionResult

DEFAULT_MODEL = "claude-sonnet-5"


def build_relay_llm(model: str = DEFAULT_MODEL) -> Runnable:
    """Structured-output-bound chat model for relay-request detection and
    extraction. `.invoke(prompt: str) -> RelayExtractionResult`."""
    chat = ChatAnthropic(
        model=model,
        api_key=settings.anthropic_api_key,
        temperature=0,
        timeout=30,
        max_retries=2,
    )
    return chat.with_structured_output(RelayExtractionResult)
