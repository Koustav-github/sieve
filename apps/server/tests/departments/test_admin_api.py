import pytest

from app.departments.admin_api import provision_department
from app.models.department import Department
from app.models.platform_connection import PlatformConnection


class _FakeClient:
    def __init__(self, install_response=None, conversations=None):
        self.install_calls = []
        self._install_response = install_response or {"id": "conn-slack-new"}
        self._conversations = conversations if conversations is not None else [
            {"id": "chan-finance-real", "name": "finance-team"},
        ]

    def install_slack(self, **kwargs):
        self.install_calls.append(kwargs)
        return self._install_response

    def list_conversations(self, connection_id):
        return self._conversations


def test_provision_department_creates_platform_connection_on_first_department(db_session):
    client = _FakeClient()

    department = provision_department(
        db_session, client,
        team_name="finance", lead_name="Alice", lead_email="alice@company.com",
        platform="slack", channel_name="finance-team",
    )

    assert department.team_name == "finance"
    assert department.channel_ref == "chan-finance-real"
    platform_connection = db_session.query(PlatformConnection).filter_by(platform="slack").one()
    assert platform_connection.connection_id == "conn-slack-new"
    assert len(client.install_calls) == 1


def test_provision_department_reuses_existing_platform_connection(db_session):
    existing = PlatformConnection(platform="slack", connection_id="conn-slack-existing")
    db_session.add(existing)
    db_session.commit()
    client = _FakeClient(conversations=[{"id": "chan-hr-real", "name": "hr-team"}])

    department = provision_department(
        db_session, client,
        team_name="hr", lead_name="Bob", lead_email="bob@company.com",
        platform="slack", channel_name="hr-team",
    )

    assert department.channel_ref == "chan-hr-real"
    assert db_session.query(PlatformConnection).filter_by(platform="slack").count() == 1
    assert client.install_calls == []


def test_provision_department_raises_when_channel_not_found():
    from unittest.mock import MagicMock

    db_session = MagicMock()
    db_session.query.return_value.filter_by.return_value.one_or_none.return_value = None
    client = _FakeClient(conversations=[])

    with pytest.raises(ValueError, match="finance-team"):
        provision_department(
            db_session, client,
            team_name="finance", lead_name="Alice", lead_email="alice@company.com",
            platform="slack", channel_name="finance-team",
        )


def test_provision_department_rolls_back_when_install_fails(db_session):
    class _FailingClient(_FakeClient):
        def install_slack(self, **kwargs):
            raise RuntimeError("Caspian install failed")

    with pytest.raises(RuntimeError, match="Caspian install failed"):
        provision_department(
            db_session, _FailingClient(),
            team_name="finance", lead_name="Alice", lead_email="alice@company.com",
            platform="slack", channel_name="finance-team",
        )

    assert db_session.query(PlatformConnection).count() == 0
    assert db_session.query(Department).count() == 0
