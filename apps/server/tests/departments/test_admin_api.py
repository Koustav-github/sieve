import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import get_db
from app.departments.admin_api import (
    ChannelResolutionError,
    DuplicateDepartmentError,
    _install_platform_connection,
    provision_department,
)
from app.main import app
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


def test_provision_department_rejects_duplicate_team_name(db_session):
    client = _FakeClient()
    provision_department(
        db_session, client,
        team_name="finance", lead_name="Alice", lead_email="alice@company.com",
        platform="slack", channel_name="finance-team",
    )

    with pytest.raises(DuplicateDepartmentError):
        provision_department(
            db_session, client,
            team_name="finance", lead_name="Bob", lead_email="bob@company.com",
            platform="slack", channel_name="finance-team",
        )


def test_provision_department_rejects_duplicate_channel_ref(db_session):
    """Two departments resolving to the same (platform_connection, channel)
    would make app.departments.registry.match_group_message() raise
    MultipleResultsFound - which the ingest handler doesn't catch, silently
    dropping every message on that channel. Must be rejected at write time."""
    client = _FakeClient(conversations=[{"id": "chan-shared", "name": "shared-team"}])
    provision_department(
        db_session, client,
        team_name="finance", lead_name="Alice", lead_email="alice@company.com",
        platform="slack", channel_name="shared-team",
    )

    with pytest.raises(DuplicateDepartmentError):
        provision_department(
            db_session, client,
            team_name="finance-backup", lead_name="Bob", lead_email="bob@company.com",
            platform="slack", channel_name="shared-team",
        )


def test_provision_department_rejects_second_exempt_department(db_session):
    client = _FakeClient(conversations=[{"id": "chan-cc", "name": "cc-team"}])
    provision_department(
        db_session, client,
        team_name="customercare", lead_name="Alice", lead_email="alice@company.com",
        platform="slack", channel_name="cc-team", requires_verification=False,
    )
    client2 = _FakeClient(conversations=[{"id": "chan-support", "name": "support-team"}])

    with pytest.raises(DuplicateDepartmentError, match="already exists"):
        provision_department(
            db_session, client2,
            team_name="support", lead_name="Bob", lead_email="bob@company.com",
            platform="slack", channel_name="support-team", requires_verification=False,
        )


@pytest.fixture()
def http_client(db_session, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_create_department_rejects_missing_api_key(http_client):
    response = http_client.post(
        "/admin/departments",
        json={
            "team_name": "finance", "lead_name": "Alice", "lead_email": "alice@company.com",
            "platform": "slack", "channel_name": "finance-team",
        },
    )
    assert response.status_code == 401


def test_create_department_rejects_wrong_api_key(http_client):
    response = http_client.post(
        "/admin/departments",
        json={
            "team_name": "finance", "lead_name": "Alice", "lead_email": "alice@company.com",
            "platform": "slack", "channel_name": "finance-team",
        },
        headers={"X-Admin-Api-Key": "wrong-key"},
    )
    assert response.status_code == 401


def test_create_department_rejects_all_requests_when_admin_api_key_unset(db_session, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        response = TestClient(app).post(
            "/admin/departments",
            json={
                "team_name": "finance", "lead_name": "Alice", "lead_email": "alice@company.com",
                "platform": "slack", "channel_name": "finance-team",
            },
            headers={"X-Admin-Api-Key": "anything"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 503


def test_create_department_succeeds_with_valid_key(http_client, monkeypatch):
    monkeypatch.setattr(
        "caspian_sdk.CommClient",
        lambda: _FakeClient(conversations=[{"id": "chan-1", "name": "finance-team"}]),
    )

    response = http_client.post(
        "/admin/departments",
        json={
            "team_name": "finance", "lead_name": "Alice", "lead_email": "alice@company.com",
            "platform": "slack", "channel_name": "finance-team",
        },
        headers={"X-Admin-Api-Key": "test-admin-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["team_name"] == "finance"
    assert body["channel_ref"] == "chan-1"
    assert body["requires_verification"] is True


def test_create_department_maps_duplicate_team_name_to_409(http_client, monkeypatch):
    monkeypatch.setattr(
        "caspian_sdk.CommClient",
        lambda: _FakeClient(conversations=[{"id": "chan-1", "name": "finance-team"}]),
    )
    payload = {
        "team_name": "finance", "lead_name": "Alice", "lead_email": "alice@company.com",
        "platform": "slack", "channel_name": "finance-team",
    }
    first = http_client.post(
        "/admin/departments", json=payload, headers={"X-Admin-Api-Key": "test-admin-key"}
    )
    assert first.status_code == 200

    second = http_client.post(
        "/admin/departments", json=payload, headers={"X-Admin-Api-Key": "test-admin-key"}
    )
    assert second.status_code == 409


def test_create_department_maps_channel_resolution_error_to_400_without_leaking_details(
    http_client, monkeypatch
):
    monkeypatch.setattr("caspian_sdk.CommClient", lambda: _FakeClient(conversations=[]))

    response = http_client.post(
        "/admin/departments",
        json={
            "team_name": "finance", "lead_name": "Alice", "lead_email": "alice@company.com",
            "platform": "slack", "channel_name": "finance-team",
        },
        headers={"X-Admin-Api-Key": "test-admin-key"},
    )

    assert response.status_code == 400
    assert "conn-" not in response.text


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
