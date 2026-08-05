from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employee import Employee


def verify_employment_id(db: Session, employment_id: str) -> Employee | None:
    """Look up an employment ID against the employees table. Returns None on
    no match. Raises on a genuine DB error - callers (app.relay.pipeline)
    must treat any exception here as "cannot verify" and fail closed, not
    as a match."""
    return db.execute(
        select(Employee).where(Employee.employment_id == employment_id)
    ).scalar_one_or_none()
