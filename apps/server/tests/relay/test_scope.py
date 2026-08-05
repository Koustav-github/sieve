from app.models.department import Department
from app.models.platform_connection import PlatformConnection
from app.relay.scope import classify_scope


def test_classify_scope_returns_group_for_matched_department(db_session):
    platform_connection = PlatformConnection(platform="slack", connection_id="conn-slack-1")
    db_session.add(platform_connection)
    db_session.flush()
    department = Department(
        team_name="finance", lead_name="Alice", lead_email="alice@company.com",
        platform_connection_id=platform_connection.id, channel_ref="chan-finance",
    )
    db_session.add(department)
    db_session.commit()

    scope, matched = classify_scope(db_session, connection_id="conn-slack-1", channel_ref="chan-finance")

    assert scope == "group"
    assert matched is not None
    assert matched.team_name == "finance"


def test_classify_scope_returns_personal_for_unmatched_channel(db_session):
    scope, matched = classify_scope(db_session, connection_id="conn-slack-1", channel_ref="chan-random")

    assert scope == "personal"
    assert matched is None


def test_classify_scope_returns_personal_when_channel_ref_is_none(db_session):
    scope, matched = classify_scope(db_session, connection_id="conn-slack-1", channel_ref=None)

    assert scope == "personal"
    assert matched is None
