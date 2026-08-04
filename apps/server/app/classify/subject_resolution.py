from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.person import PersonEntity


def resolve_person_by_display_name(db: Session, name: str) -> PersonEntity | None:
    """Best-effort match against existing person_entities.display_name,
    case-insensitive exact match. Most entities created via sender resolution
    are provisional with display_name=None, so this will often miss - that's
    expected and handled by the caller (falls back to storing raw text). Full
    cross-channel identity resolution is out of scope (spec §9 stretch goal).

    `display_name` has no unique constraint, so multiple rows can match (e.g.
    two different people named "John Smith", or duplicate provisional
    entities). Rather than raise `MultipleResultsFound` and let the exception
    propagate out of the classification graph - which would roll back and
    silently drop the whole RoutingDecision - we deterministically return the
    lowest-id match. Picking the "wrong" duplicate is a much smaller problem
    than dropping the message's classification entirely.
    """
    return (
        db.execute(
            select(PersonEntity)
            .where(func.lower(PersonEntity.display_name) == name.lower())
            .order_by(PersonEntity.id)
        )
        .scalars()
        .first()
    )
