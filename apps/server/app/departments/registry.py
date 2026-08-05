from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.department import Department
from app.models.platform_connection import PlatformConnection


def get_department(db: Session, team_name: str) -> Department | None:
    return db.execute(
        select(Department).where(Department.team_name == team_name)
    ).scalar_one_or_none()


def list_departments(db: Session) -> list[Department]:
    return list(db.execute(select(Department)).scalars().all())


def resolve_target(db: Session, extracted_text: str) -> Department | None:
    """Case-insensitive exact match against team_name. No fuzzy matching -
    the relay-detection LLM is expected to echo back a name close enough to
    match exactly; broaden this if that assumption proves wrong once real
    departments are registered."""
    return db.execute(
        select(Department).where(func.lower(Department.team_name) == extracted_text.lower())
    ).scalar_one_or_none()


def get_exempt_department(db: Session) -> Department | None:
    """The one department with requires_verification=False, used as the
    group-chat fallback target and the personal-DM verification-skip check.
    Raises RuntimeError if more than one exists - the spec explicitly calls
    for failing loud rather than silently picking one in that case, since
    it's a data-integrity problem an admin needs to fix, not a routing
    decision this function should make silently."""
    exempt = list(
        db.execute(
            select(Department).where(Department.requires_verification.is_(False))
        ).scalars()
    )
    if len(exempt) > 1:
        raise RuntimeError(
            f"More than one department has requires_verification=False: "
            f"{[d.team_name for d in exempt]!r} - exactly zero or one is expected"
        )
    return exempt[0] if exempt else None


def match_group_message(db: Session, *, connection_id: str, channel_ref: str) -> Department | None:
    return db.execute(
        select(Department)
        .join(PlatformConnection, Department.platform_connection_id == PlatformConnection.id)
        .where(PlatformConnection.connection_id == connection_id, Department.channel_ref == channel_ref)
    ).scalar_one_or_none()
