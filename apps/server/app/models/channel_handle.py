from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.person import PersonEntity


class ChannelHandle(Base):
    __tablename__ = "channel_handles"
    __table_args__ = (
        UniqueConstraint("channel", "handle", name="uq_channel_handles_channel_handle"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_entity_id: Mapped[int] = mapped_column(ForeignKey("person_entities.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    handle: Mapped[str] = mapped_column(String, nullable=False)

    person_entity: Mapped["PersonEntity"] = relationship()
