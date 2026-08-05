from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.message import Message

RELAY_STATUSES = ("pending", "completed")


class RelayRequest(Base):
    __tablename__ = "relay_requests"
    __table_args__ = (
        UniqueConstraint(
            "target_conversation_id", name="uq_relay_requests_target_conversation_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    source_identity: Mapped[str] = mapped_column(String, nullable=False)
    target_identity: Mapped[str] = mapped_column(String, nullable=False)
    target_conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    message_text: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_message: Mapped["Message"] = relationship()
