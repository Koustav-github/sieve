import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.department import Department
from app.models.platform_connection import PlatformConnection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/departments", tags=["admin"])

# Maps a platform name to the CommClient method that installs/connects it.
# Only "slack" uses the one-click install_*() flow today. Discord's real
# CommClient method is `connect_discord`, which needs a `username=` kwarg
# this endpoint doesn't collect yet (see app/ingest/identities.py for the
# live-verified method names) - and email/telegram need different args
# (username/bot_token) too, so they're deliberately not supported here.
# Extend this dict (and _install_platform_connection's kwargs) when that's
# needed rather than silently mismapping a platform to the wrong method.
_INSTALL_METHODS = {"slack": "install_slack"}


class DepartmentCreateRequest(BaseModel):
    team_name: str
    lead_name: str
    lead_email: str
    platform: str
    channel_name: str


class DepartmentResponse(BaseModel):
    id: int
    team_name: str
    lead_name: str
    lead_email: str
    platform: str
    channel_ref: str
    requires_verification: bool


class ChannelResolutionError(ValueError):
    """Raised by `_resolve_channel_ref` when no exact channel match (or more
    than one) is found. Its message carries the connection_id and visible-
    conversation counts, which are useful for server-side logs and direct
    callers/tests of `provision_department` but must NOT be echoed to HTTP
    callers - see `create_department`'s exception handling below."""


def _normalize_channel_name(name: str) -> str:
    """Lowercases and strips a leading '#' so "finance" and "#Finance"
    compare equal."""
    return name.lower().lstrip("#")


def _resolve_channel_ref(client, connection_id: str, channel_name: str) -> str:
    """Finds the conversation matching `channel_name` on this connection.
    NOT LIVE-VERIFIED (see this plan's Global Constraints) - assumes
    list_conversations() returns dicts with an 'id' and a 'name'-like field
    the admin's channel_name hint can be matched against. Matches on exact
    normalized equality (case-insensitive, leading '#' stripped from both
    sides) rather than substring - a substring match could silently match
    an admin's "finance" to an unrelated "finance-leaks-test" channel, or
    let anyone who can name a channel in the workspace hijack a department's
    routing. Raises a clear error (not a silent guess) if nothing matches -
    or if more than one conversation shares the exact normalized name, since
    that's ambiguous and picking the first would be a guess - since the
    likely causes (bot not yet invited to the channel, a typo'd
    channel_name, or a workspace naming collision) all need a human to
    notice and fix, not a fallback to guess at."""
    conversations = client.list_conversations(connection_id)
    target = _normalize_channel_name(channel_name)
    matches = [
        conversation
        for conversation in conversations
        if _normalize_channel_name(conversation.get("name") or "") == target
    ]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        raise ChannelResolutionError(
            f"Ambiguous channel_name={channel_name!r}: {len(matches)} conversations "
            f"on connection {connection_id!r} share that exact name - "
            "rename one of them or disambiguate before retrying"
        )
    raise ChannelResolutionError(
        f"No conversation matching channel_name={channel_name!r} found on "
        f"connection {connection_id!r} ({len(conversations)} conversation(s) "
        "visible) - invite the bot to the channel first, then retry"
    )


def _install_platform_connection(client, platform: str) -> str:
    method_name = _INSTALL_METHODS.get(platform)
    if method_name is None:
        raise ValueError(
            f"Platform {platform!r} is not yet supported by this endpoint - "
            f"supported: {sorted(_INSTALL_METHODS)}"
        )
    connection = getattr(client, method_name)()
    return connection["id"]


def provision_department(
    db: Session,
    client,
    *,
    team_name: str,
    lead_name: str,
    lead_email: str,
    platform: str,
    channel_name: str,
) -> Department:
    """Registers a department, provisioning a new platform_connections row
    (live Caspian call) only if this platform has no connection yet -
    departments on an already-connected platform reuse the existing row.
    Rolls back on any failure so a half-created department/connection never
    persists."""
    platform_connection = (
        db.query(PlatformConnection).filter_by(platform=platform).one_or_none()
    )
    try:
        if platform_connection is None:
            connection_id = _install_platform_connection(client, platform)
            platform_connection = PlatformConnection(platform=platform, connection_id=connection_id)
            db.add(platform_connection)
            db.flush()

        channel_ref = _resolve_channel_ref(client, platform_connection.connection_id, channel_name)

        department = Department(
            team_name=team_name,
            lead_name=lead_name,
            lead_email=lead_email,
            platform_connection_id=platform_connection.id,
            channel_ref=channel_ref,
        )
        db.add(department)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return department


@router.post("", response_model=DepartmentResponse)
def create_department(
    payload: DepartmentCreateRequest, db: Session = Depends(get_db)
) -> DepartmentResponse:
    from caspian_sdk import CommClient

    client = CommClient()
    try:
        department = provision_department(
            db, client,
            team_name=payload.team_name, lead_name=payload.lead_name,
            lead_email=payload.lead_email, platform=payload.platform,
            channel_name=payload.channel_name,
        )
    except ChannelResolutionError as exc:
        # The precise message (connection_id, visible-conversation counts,
        # etc.) is logged server-side only - echoing it back to the HTTP
        # caller would leak internal Caspian connection identifiers and
        # workspace visibility info to whoever hits this endpoint.
        logger.exception("Failed to provision department %r", payload.team_name)
        raise HTTPException(
            status_code=400, detail="Channel not found or bot not invited to it"
        ) from exc
    except ValueError as exc:
        # Other ValueErrors (e.g. an unsupported platform) don't carry
        # sensitive internal details, so it's safe to pass them through.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to provision department %r", payload.team_name)
        raise HTTPException(status_code=502, detail="Failed to provision department") from exc

    return DepartmentResponse(
        id=department.id,
        team_name=department.team_name,
        lead_name=department.lead_name,
        lead_email=department.lead_email,
        platform=department.platform_connection.platform,
        channel_ref=department.channel_ref,
        requires_verification=department.requires_verification,
    )
