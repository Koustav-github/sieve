from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.message import Message

RELAY_STATUSES = ("pending", "completed")


class RelayRequest(Base):
    __tablename__ = "relay_requests"
    __table_args__ = (
        # Partial (not table-wide) unique index: only one *pending* relay may
        # own a given target_conversation_id at a time. A table-wide unique
        # constraint would let one completed relay to a lead permanently
        # block every future relay to that same conversation - and worse,
        # would force a real duplicate-conversation-id send (Caspian's
        # shared-mailbox risk, see dispatcher.py) to silently fail the DB
        # insert *after* the email already went out, misrouting the lead's
        # eventual reply to whichever RelayRequest happened to win the race.
        Index(
            "uq_relay_requests_pending_target_conversation_id",
            "target_conversation_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
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
