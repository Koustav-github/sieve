import pytest

from app.departments.admin_api import (
    ChannelResolutionError,
    _install_platform_connection,
    provision_department,
)
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


def test_provision_department_raises_when_channel_not_found_and_rolls_back_flushed_connection(db_session):
    """The highest-risk failure mode: install_slack() succeeds (so a new
    PlatformConnection gets created and flushed to the DB) but the channel
    can't be resolved afterward. Uses the real db_session fixture (not a
    mock, which would give no-op .rollback()/.query() and only prove an
    exception propagates) so this actually exercises SQLAlchemy's rollback
    path - proving the flushed-but-uncommitted connection row really gets
    discarded rather than leaking into the platform_connections table and
    corrupting every future registration attempt on that platform."""
    client = _FakeClient(conversations=[])  # list_conversations() -> [] -> no match

    with pytest.raises(ChannelResolutionError, match="finance-team"):
        provision_department(
            db_session, client,
            team_name="finance", lead_name="Alice", lead_email="alice@company.com",
            platform="slack", channel_name="finance-team",
        )

    assert db_session.query(PlatformConnection).count() == 0
    assert db_session.query(Department).count() == 0


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


def test_install_platform_connection_raises_for_discord():
    """Discord is deliberately unsupported: the real caspian_sdk.CommClient
    method is `connect_discord` (see app/ingest/identities.py), not
    `install_discord` - registering the first department on Discord must
    raise a clear error, not silently call a method that doesn't exist."""
    client = _FakeClient()

    with pytest.raises(ValueError, match="discord.*not yet supported"):
        _install_platform_connection(client, "discord")


def test_resolve_channel_ref_exact_match_still_works(db_session):
    client = _FakeClient(conversations=[{"id": "chan-1", "name": "finance-team"}])

    department = provision_department(
        db_session, client,
        team_name="finance", lead_name="Alice", lead_email="alice@company.com",
        platform="slack", channel_name="finance-team",
    )

    assert department.channel_ref == "chan-1"


def test_resolve_channel_ref_rejects_substring_only_match(db_session):
    """A channel named "finance-team-extended" must NOT match a request for
    "finance" - substring matching would let an unrelated (or attacker-
    controlled) channel silently hijack the department's routing."""
    client = _FakeClient(conversations=[{"id": "chan-1", "name": "finance-team-extended"}])

    with pytest.raises(ChannelResolutionError, match="finance"):
        provision_department(
            db_session, client,
            team_name="finance", lead_name="Alice", lead_email="alice@company.com",
            platform="slack", channel_name="finance",
        )


def test_resolve_channel_ref_raises_when_two_conversations_share_exact_name(db_session):
    client = _FakeClient(
        conversations=[
            {"id": "chan-1", "name": "finance-team"},
            {"id": "chan-2", "name": "Finance-Team"},
        ]
    )

    with pytest.raises(ChannelResolutionError, match="Ambiguous"):
        provision_department(
            db_session, client,
            team_name="finance", lead_name="Alice", lead_email="alice@company.com",
            platform="slack", channel_name="finance-team",
        )
