import dataclasses
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
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


def _raw_payload(message: Any) -> dict[str, Any]:
    """Capture the full raw message for downstream use (e.g. the classification
    sub-project needs `subject` - it's the primary bucketing signal for email).

    Prefers ``dataclasses.fields()`` so this stays correct against the real
    ``caspian_sdk.client.Message`` dataclass (id, conversation_id,
    connection_id, customer_id, agent_id, channel, sender, subject, text,
    html, media, ...) without hand-picking fields that will drift as the SDK
    evolves. Falls back to ``vars()`` for the simple fake message objects
    (``SimpleNamespace``) used in tests, which aren't real dataclasses.
    Drops any leading-underscore attribute either way - in particular
    ``_client`` on the real ``Message``, which holds a live SDK client and
    isn't serializable.
    """
    try:
        fields = dataclasses.fields(message)
    except TypeError:
        data = dict(vars(message))
    else:
        data = {f.name: getattr(message, f.name, None) for f in fields}
    return {key: value for key, value in data.items() if not key.startswith("_")}


def build_on_message_handler(session_factory: Callable[[], Session]) -> Callable[[Any], None]:
    def handle(message: Any) -> None:
        db = session_factory()
        message_id = None
        try:
            message_id = _field(message, "id")
            if message_id is None:
                logger.error("Dropping message with no id: %r", message)
                return

            if is_duplicate(db, message_id):
                return

            channel = _field(message, "channel")
            agent_id = _field(message, "agent_id")
            # The real caspian_sdk.Message has no `thread_id` - the field is
            # `conversation_id`. Keep `thread_id` as a secondary fallback for
            # robustness against the simpler fake message objects tests use.
            thread_id = _field(message, "conversation_id", "thread_id")
            sender = getattr(message, "sender", None) or {}
            # Extract sender handle with fallback keys for flexibility
            if isinstance(sender, dict):
                sender_handle = sender.get("address") or sender.get("email") or sender.get("handle")
            else:
                sender_handle = None

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
                raw_payload=_raw_payload(message),
            )
            db.commit()
        except IntegrityError:
            # Caspian's listen() can dispatch different conversations
            # concurrently, so two first-contact messages from the same new
            # sender can race on the channel_handles unique constraint. The
            # loser here is a valid message, not a bug - log it distinctly
            # (no traceback) and treat it as "already processed" by dropping
            # it, rather than as a crash-worthy failure.
            db.rollback()
            logger.warning(
                "IntegrityError persisting message %s; likely a concurrent "
                "sender/dedup race on channel_handles - treating as already "
                "processed and dropping",
                message_id,
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to handle message %r", message_id)
        finally:
            db.close()

    return handle
