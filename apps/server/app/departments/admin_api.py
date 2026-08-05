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
# Only "slack"/"discord" use the one-click install_*() flow today - email
# and telegram need different args (username/bot_token) this endpoint
# doesn't collect yet, so they're deliberately not supported here. Extend
# this dict (and _install_platform_connection's kwargs) when that's needed.
_INSTALL_METHODS = {"slack": "install_slack", "discord": "install_discord"}


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


def _resolve_channel_ref(client, connection_id: str, channel_name: str) -> str:
    """Finds the conversation matching `channel_name` on this connection.
    NOT LIVE-VERIFIED (see this plan's Global Constraints) - assumes
    list_conversations() returns dicts with an 'id' and a 'name'-like field
    the admin's channel_name hint can be matched against. Raises a clear
    error (not a silent guess) if nothing matches, since the two most likely
    causes - bot not yet invited to the channel, or a typo'd channel_name -
    both need a human to notice and fix, not a fallback to guess at."""
    conversations = client.list_conversations(connection_id)
    channel_name_lower = channel_name.lower()
    for conversation in conversations:
        name = conversation.get("name") or ""
        if channel_name_lower in name.lower():
            return conversation["id"]
    raise ValueError(
        f"No conversation matching channel_name={channel_name!r} found on "
        f"connection {connection_id!r} ({len(conversations)} conversation(s) "
        "visible) - invite the bot to the channel first, then retry"
    )


def _install_platform_connection(client, platform: str) -> str:
    method_name = _INSTALL_METHODS.get(platform)
    if method_name is None:
        raise ValueError(
            f"Unsupported platform {platform!r} - supported: {sorted(_INSTALL_METHODS)}"
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
    except ValueError as exc:
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
