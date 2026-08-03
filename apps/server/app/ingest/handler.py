import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.ingest.message_store import is_duplicate, persist_message
from app.ingest.sender_resolution import resolve_sender

logger = logging.getLogger(__name__)


def _field(message: Any, *names: str) -> Any:
    for name in names:
        value = getattr(message, name, None)
        if value is not None:
            return value
    return None


def build_on_message_handler(session_factory: Callable[[], Session]) -> Callable[[Any], None]:
    def handle(message: Any) -> None:
        db = session_factory()
        try:
            message_id = _field(message, "id", "message_id")
            if message_id is None:
                logger.error("Dropping message with no id: %r", message)
                return

            if is_duplicate(db, message_id):
                return

            channel = _field(message, "channel")
            agent_id = _field(message, "agent_id", "identity")
            thread_id = _field(message, "thread_id")
            sender = getattr(message, "sender", None) or {}
            # Extract sender handle with fallback keys for flexibility
            if isinstance(sender, dict):
                sender_handle = sender.get("address") or sender.get("email") or sender.get("handle")
            else:
                sender_handle = None
            text = _field(message, "text")

            if not (channel and agent_id and sender_handle):
                logger.error(
                    "Dropping message %s: missing required field(s) "
                    "(channel=%r agent_id=%r sender=%r)",
                    message_id,
                    channel,
                    agent_id,
                    sender_handle,
                )
                return

            resolve_sender(db, channel=channel, handle=sender_handle)
            persist_message(
                db,
                caspian_message_id=message_id,
                agent_id=agent_id,
                channel=channel,
                sender_handle=sender_handle,
                thread_id=thread_id,
                raw_payload={
                    "text": text,
                    "sender": sender,
                    "channel": channel,
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to handle message %r", message_id)
        finally:
            db.close()

    return handle
