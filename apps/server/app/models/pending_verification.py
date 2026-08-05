from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.department import Department


class PendingVerification(Base):
    __tablename__ = "pending_verifications"
    __table_args__ = (
        UniqueConstraint("sender_handle", "channel", name="uq_pending_verifications_sender_channel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_handle: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    target_department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    message_text: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    target_department: Mapped["Department"] = relationship()
