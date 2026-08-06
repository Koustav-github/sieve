from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.platform_connection import PlatformConnection


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("team_name", name="uq_departments_team_name"),
        # Prevents two departments from resolving to the same channel: an
        # admin mistake here would otherwise make match_group_message()
        # raise MultipleResultsFound inside the ingest handler's try block,
        # silently dropping every message on that channel (never persisted).
        UniqueConstraint(
            "platform_connection_id", "channel_ref",
            name="uq_departments_platform_connection_channel_ref",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_name: Mapped[str] = mapped_column(String, nullable=False)
    lead_name: Mapped[str] = mapped_column(String, nullable=False)
    lead_email: Mapped[str] = mapped_column(String, nullable=False)
    platform_connection_id: Mapped[int] = mapped_column(
        ForeignKey("platform_connections.id"), nullable=False
    )
    channel_ref: Mapped[str] = mapped_column(String, nullable=False)
    requires_verification: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    platform_connection: Mapped["PlatformConnection"] = relationship()
