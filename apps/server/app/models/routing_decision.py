from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.bucket import Bucket
from app.models.message import Message
from app.models.person import PersonEntity


class RoutingDecision(Base):
    __tablename__ = "routing_decisions"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_routing_decisions_message_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    deciding_layer: Mapped[str] = mapped_column(String, nullable=False)
    bucket_id: Mapped[int | None] = mapped_column(ForeignKey("buckets.id"), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    subject_person_entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("person_entities.id"), nullable=True
    )
    subject_raw_text: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    message: Mapped["Message"] = relationship()
    bucket: Mapped["Bucket | None"] = relationship()
    subject_person_entity: Mapped["PersonEntity | None"] = relationship()
