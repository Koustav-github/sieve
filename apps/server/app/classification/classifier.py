import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.classification import layer1, layer2
from app.models.message import Message

logger = logging.getLogger(__name__)


def classify(
    session_factory: Callable[[], Session],
    pinecone_client: Any | None,
    groq_client: Any | None,
    message_id: int,
) -> None:
    db = session_factory()
    try:
        message = db.get(Message, message_id)
        if message is None:
            logger.warning("classify: message %s not found", message_id)
            return

        coarse_bucket = message.agent_id
        text = message.raw_payload.get("text") or ""

        layer1_match = _try_layer1(text, coarse_bucket, pinecone_client, message_id)
        if layer1_match is not None:
            message.fine_bucket = layer1_match.fine_bucket
            message.classified_by = "layer1"
            message.classified_at = datetime.now(UTC)
            db.commit()
            return

        layer2_result = _try_layer2(text, coarse_bucket, groq_client, message_id)
        if layer2_result is not None:
            message.fine_bucket = layer2_result.fine_bucket
            message.classified_by = "layer2"
            message.confidence = layer2_result.confidence
            message.classified_at = datetime.now(UTC)
            db.commit()
            return

        logger.info("Message %s could not be classified by either layer", message_id)
    except Exception:
        db.rollback()
        logger.exception("Unexpected error classifying message %s", message_id)
    finally:
        db.close()


def _try_layer1(
    text: str, coarse_bucket: str, pinecone_client: Any | None, message_id: int
) -> layer1.Layer1Match | None:
    try:
        return layer1.match(text, coarse_bucket, pinecone_client)
    except Exception:
        logger.exception("Layer 1 raised unexpectedly for message %s", message_id)
        return None


def _try_layer2(
    text: str, coarse_bucket: str, groq_client: Any | None, message_id: int
) -> layer2.Layer2Result | None:
    try:
        return layer2.classify(groq_client, text, coarse_bucket)
    except Exception:
        logger.exception("Layer 2 raised unexpectedly for message %s", message_id)
        return None
