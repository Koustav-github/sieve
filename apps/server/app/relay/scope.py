from sqlalchemy.orm import Session

from app.departments.registry import match_group_message
from app.models.department import Department


def classify_scope(
    db: Session, *, connection_id: str, channel_ref: str | None
) -> tuple[str, Department | None]:
    """Returns ("group", department) if this message arrived on a
    registered department's own channel, else ("personal", None).

    NOT LIVE-VERIFIED (see this plan's Global Constraints #2): there is no
    positive "this is a DM" signal confirmed on the real Caspian SDK's
    Message dataclass, so this defaults any unmatched channel to "personal"
    rather than trying to detect DM-ness directly. This means a group
    channel the bot is present in but that hasn't been registered as a
    department is currently treated as a personal chat with whoever
    messaged there - an accepted v1-of-this-feature limitation, not an
    oversight; tighten this once Caspian's actual conversation-type signal
    is confirmed.
    """
    if channel_ref is None:
        return "personal", None
    department = match_group_message(db, connection_id=connection_id, channel_ref=channel_ref)
    if department is not None:
        return "group", department
    return "personal", None
