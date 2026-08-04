from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.bucket import Bucket

RULE_TYPES = ("keyword", "sender_allowlist", "domain")


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    bucket_id: Mapped[int] = mapped_column(ForeignKey("buckets.id"), nullable=False)
    rule_type: Mapped[str] = mapped_column(String, nullable=False)
    pattern: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    bucket: Mapped["Bucket"] = relationship()
