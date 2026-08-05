from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PersonEntity(Base):
    __tablename__ = "person_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verified_employee: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
