from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.channel_handle import ChannelHandle
from app.models.person import PersonEntity


def resolve_sender(db: Session, *, channel: str, handle: str) -> PersonEntity:
    existing = db.execute(
        select(ChannelHandle).where(
            ChannelHandle.channel == channel, ChannelHandle.handle == handle
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.person_entity

    person = PersonEntity(display_name=None, is_provisional=True)
    db.add(person)
    db.flush()

    channel_handle = ChannelHandle(person_entity_id=person.id, channel=channel, handle=handle)
    db.add(channel_handle)
    db.flush()
    return person
