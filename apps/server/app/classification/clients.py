import logging

from groq import Groq
from pinecone import Pinecone

from app.core.config import settings

logger = logging.getLogger(__name__)

PINECONE_EMBED_MODEL = "multilingual-e5-large"


def build_pinecone_client() -> Pinecone | None:
    if not settings.pinecone_api_key:
        logger.warning(
            "PINECONE_API_KEY is blank; semantic-similarity classification "
            "signal disabled"
        )
        return None
    try:
        return Pinecone(api_key=settings.pinecone_api_key)
    except Exception:
        logger.exception(
            "Failed to construct Pinecone client; semantic-similarity "
            "classification signal disabled"
        )
        return None


def build_groq_client() -> Groq | None:
    if not settings.groq_api_key:
        logger.warning(
            "GROQ_API_KEY is blank; Layer 2 LLM classification fallback disabled"
        )
        return None
    try:
        return Groq(api_key=settings.groq_api_key)
    except Exception:
        logger.exception(
            "Failed to construct Groq client; Layer 2 LLM classification "
            "fallback disabled"
        )
        return None
