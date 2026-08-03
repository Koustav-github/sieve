from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.message import Message


def is_duplicate(db: Session, caspian_message_id: str) -> bool:
    existing = db.execute(
        select(Message.id).where(Message.caspian_message_id == caspian_message_id)
    ).scalar_one_or_none()
    return existing is not None


def persist_message(
    db: Session,
    *,
    caspian_message_id: str,
    agent_id: str,
    channel: str,
    sender_handle: str,
    thread_id: str | None,
    raw_payload: dict[str, Any],
) -> Message:
    message = Message(
        caspian_message_id=caspian_message_id,
        agent_id=agent_id,
        channel=channel,
        sender_handle=sender_handle,
        thread_id=thread_id,
        raw_payload=raw_payload,
        received_at=datetime.now(timezone.utc),
    )
    db.add(message)
    db.flush()
    return message
