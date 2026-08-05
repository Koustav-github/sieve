import pytest

from app.departments.registry import (
    get_department,
    get_exempt_department,
    list_departments,
    match_group_message,
    resolve_target,
)
from app.models.department import Department
from app.models.platform_connection import PlatformConnection


def _make_department(db_session, *, team_name, connection_id="conn-slack-1", channel_ref, requires_verification=True):
    platform_connection = (
        db_session.query(PlatformConnection).filter_by(connection_id=connection_id).one_or_none()
    )
    if platform_connection is None:
        platform_connection = PlatformConnection(platform="slack", connection_id=connection_id)
        db_session.add(platform_connection)
        db_session.flush()
    department = Department(
        team_name=team_name, lead_name="Lead", lead_email=f"{team_name}@company.com",
        platform_connection_id=platform_connection.id, channel_ref=channel_ref,
        requires_verification=requires_verification,
    )
    db_session.add(department)
    db_session.commit()
    return department


def test_get_department_returns_match_by_team_name(db_session):
    _make_department(db_session, team_name="finance", channel_ref="chan-finance")

    result = get_department(db_session, "finance")

    assert result is not None
    assert result.team_name == "finance"


def test_get_department_returns_none_for_unknown_team(db_session):
    assert get_department(db_session, "does-not-exist") is None


def test_list_departments_returns_all(db_session):
    _make_department(db_session, team_name="finance", channel_ref="chan-finance")
    _make_department(db_session, team_name="hr", channel_ref="chan-hr")

    result = list_departments(db_session)

    assert {d.team_name for d in result} == {"finance", "hr"}


def test_resolve_target_matches_case_insensitively(db_session):
    _make_department(db_session, team_name="finance", channel_ref="chan-finance")

    result = resolve_target(db_session, "Finance")

    assert result is not None
    assert result.team_name == "finance"


def test_resolve_target_returns_none_for_no_match(db_session):
    assert resolve_target(db_session, "legal") is None


def test_get_exempt_department_returns_the_one_exempt_row(db_session):
    _make_department(db_session, team_name="finance", channel_ref="chan-finance")
    _make_department(db_session, team_name="customercare", channel_ref="chan-cc", requires_verification=False)

    result = get_exempt_department(db_session)

    assert result is not None
    assert result.team_name == "customercare"


def test_get_exempt_department_returns_none_when_zero_exist(db_session):
    _make_department(db_session, team_name="finance", channel_ref="chan-finance")

    assert get_exempt_department(db_session) is None


def test_get_exempt_department_raises_when_more_than_one_exists(db_session):
    _make_department(db_session, team_name="customercare", channel_ref="chan-cc", requires_verification=False)
    _make_department(db_session, team_name="support", channel_ref="chan-support", requires_verification=False)

    with pytest.raises(RuntimeError):
        get_exempt_department(db_session)


def test_match_group_message_finds_department_by_connection_and_channel(db_session):
    _make_department(db_session, team_name="finance", connection_id="conn-slack-1", channel_ref="chan-finance")

    result = match_group_message(db_session, connection_id="conn-slack-1", channel_ref="chan-finance")

    assert result is not None
    assert result.team_name == "finance"


def test_match_group_message_returns_none_for_unmatched_channel(db_session):
    _make_department(db_session, team_name="finance", connection_id="conn-slack-1", channel_ref="chan-finance")

    result = match_group_message(db_session, connection_id="conn-slack-1", channel_ref="chan-random")

    assert result is None
