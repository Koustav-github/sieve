import dataclasses
import logging
from collections.abc import Callable
from concurrent.futures import Executor
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ingest.message_store import is_duplicate, persist_message
from app.ingest.sender_resolution import resolve_sender
from app.relay.group_pipeline import run_group_relay
from app.relay.personal_pipeline import run_personal_relay
from app.relay.scope import classify_scope

logger = logging.getLogger(__name__)


def _field(message: Any, *names: str) -> Any:
    for name in names:
        value = getattr(message, name, None)
        if value is not None:
            return value
    return None


def _raw_payload(message: Any) -> dict[str, Any]:
    """Capture the full raw message for downstream use (e.g. subject is a
    signal for the relay-detection LLM).

    Prefers ``dataclasses.fields()`` so this stays correct against the real
    ``caspian_sdk.client.Message`` dataclass without hand-picking fields
    that will drift as the SDK evolves. Falls back to ``vars()`` for the
    simple fake message objects (``SimpleNamespace``) used in tests, which
    aren't real dataclasses. Drops any leading-underscore attribute either
    way - in particular ``_client``, which holds a live SDK client and
    isn't serializable.
    """
    try:
        fields = dataclasses.fields(message)
    except TypeError:
        data = dict(vars(message))
    else:
        data = {f.name: getattr(message, f.name, None) for f in fields}
    return {key: value for key, value in data.items() if not key.startswith("_")}


def _relay_and_record(
    session_factory: Callable[[], Session],
    relay_llm: Any,
    client: Any,
    relay_sender_connection_id: str,
    *,
    message_id: int,
    connection_id: str,
    channel_ref: str | None,
    channel: str,
    sender_handle: str,
    subject: str | None,
    text: str | None,
) -> None:
    """Runs on `executor`'s worker thread, off the ingest `listen()` loop -
    opens its own `Session` (the handler's session belongs to a different
    thread and is closed by the time this runs). Never raises: a relay
    failure here must not affect ingestion, which already completed
    successfully before this was submitted."""
    db = session_factory()
    try:
        scope, department = classify_scope(db, connection_id=connection_id, channel_ref=channel_ref)
        if scope == "group":
            run_group_relay(
                relay_llm, client, db,
                message_id=message_id, source_department=department,
                subject=subject, text=text,
            )
        else:
            run_personal_relay(
                relay_llm, client, db,
                connection_id=relay_sender_connection_id,
                message_id=message_id, channel=channel, sender_handle=sender_handle,
                conversation_id=channel_ref,
                arrived_on_relay_sender_connection=(connection_id == relay_sender_connection_id),
                subject=subject, text=text,
            )
    except Exception:
        db.rollback()
        logger.exception(
            "Async relay failed for message %s; message was still ingested successfully",
            message_id,
        )
    finally:
        db.close()


def build_on_message_handler(
    session_factory: Callable[[], Session],
    relay_llm: Any,
    executor: Executor,
    client: Any,
    relay_sender_connection_id: str,
) -> Callable[[Any], None]:
    """Unlike v1, there's no fixed connection_id -> identity map built at
    startup - `app.relay.scope.classify_scope` resolves group-chat
    membership from the live `departments` table per message, so a
    department registered after the worker started is immediately routable.

    `agent_id` stored on the `messages` row is best-effort here: the
    matched department's team_name for a group-chat message, or the
    literal string "personal" for a personal-DM message (there's no
    department a personal message "belongs to" until routing resolves
    one) - see this plan's Decisions section.
    """

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
            connection_id = _field(message, "connection_id")
            channel_ref = _field(message, "conversation_id")
            sender = getattr(message, "sender", None) or {}
            if isinstance(sender, dict):
                sender_handle = sender.get("address") or sender.get("email") or sender.get("handle")
            else:
                sender_handle = None

            if not (channel and connection_id and sender_handle):
                logger.error(
                    "Dropping message %s: missing required field(s) "
                    "(channel=%r connection_id=%r sender=%r)",
                    message_id, channel, connection_id, sender_handle,
                )
                return

            _, department = classify_scope(db, connection_id=connection_id, channel_ref=channel_ref)
            agent_id = department.team_name if department is not None else "personal"

            resolve_sender(db, channel=channel, handle=sender_handle)
            persisted_message = persist_message(
                db,
                caspian_message_id=message_id,
                agent_id=agent_id,
                channel=channel,
                sender_handle=sender_handle,
                thread_id=channel_ref,
                raw_payload=_raw_payload(message),
            )
            db.commit()

            executor.submit(
                _relay_and_record,
                session_factory,
                relay_llm,
                client,
                relay_sender_connection_id,
                message_id=persisted_message.id,
                connection_id=connection_id,
                channel_ref=channel_ref,
                channel=channel,
                sender_handle=sender_handle,
                subject=_field(message, "subject"),
                text=_field(message, "text"),
            )
        except IntegrityError:
            db.rollback()
            logger.warning(
                "IntegrityError persisting message %s; likely a duplicate "
                "delivery race on messages.caspian_message_id or a "
                "concurrent sender-resolution race on channel_handles - "
                "treating as already processed and dropping",
                message_id,
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to handle message %r", message_id)
        finally:
            db.close()

    return handle
