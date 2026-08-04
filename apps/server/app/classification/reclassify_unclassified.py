"""One-off script: backfill classification for messages that were ingested
while GROQ_API_KEY/PINECONE_API_KEY were blank (or otherwise failed both
classification layers) and so were left with fine_bucket = NULL.

There is no automatic retry for failed classification (by design - see
Task 8), so once real keys are added later, run this manually to work
through the backlog:
    uv run python -m app.classification.reclassify_unclassified

This is a plain, unbatched pass over every unclassified message - fine for
a hackathon-scale project, not intended to scale to a large backlog.
"""

import logging

from sqlalchemy import select

from app.classification import classifier
from app.classification.clients import build_groq_client, build_pinecone_client
from app.db.session import SessionLocal
from app.models.message import Message

logger = logging.getLogger(__name__)


def reclassify_unclassified() -> None:
    pinecone_client = build_pinecone_client()
    groq_client = build_groq_client()

    db = SessionLocal()
    try:
        message_ids = db.execute(
            select(Message.id).where(Message.fine_bucket.is_(None))
        ).scalars().all()
    finally:
        db.close()

    logger.info("Reclassifying %d unclassified message(s)", len(message_ids))
    for message_id in message_ids:
        classifier.classify(SessionLocal, pinecone_client, groq_client, message_id)
    logger.info("Reclassification pass complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    reclassify_unclassified()
