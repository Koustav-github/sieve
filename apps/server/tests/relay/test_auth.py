import pytest

from app.models.employee import Employee
from app.relay.auth import verify_employment_id


def test_verify_employment_id_returns_matching_employee(db_session):
    employee = Employee(employment_id="EMP-42", name="Alice")
    db_session.add(employee)
    db_session.commit()

    result = verify_employment_id(db_session, "EMP-42")

    assert result is not None
    assert result.id == employee.id
    assert result.name == "Alice"


def test_verify_employment_id_returns_none_for_unknown_id(db_session):
    result = verify_employment_id(db_session, "does-not-exist")

    assert result is None


def test_verify_employment_id_propagates_db_errors(db_session, monkeypatch):
    def failing_execute(*args, **kwargs):
        raise RuntimeError("DB connection lost")

    monkeypatch.setattr(db_session, "execute", failing_execute)

    with pytest.raises(RuntimeError, match="DB connection lost"):
        verify_employment_id(db_session, "EMP-42")
