import pytest
from sqlalchemy.exc import IntegrityError

from app.models.department import Department
from app.models.pending_verification import PendingVerification
from app.models.platform_connection import PlatformConnection


def test_platform_connection_platform_must_be_unique(db_session):
    db_session.add(PlatformConnection(platform="slack", connection_id="conn-slack-1"))
    db_session.commit()

    db_session.add(PlatformConnection(platform="slack", connection_id="conn-slack-2"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_department_requires_verification_defaults_true(db_session):
    platform_connection = PlatformConnection(platform="slack", connection_id="conn-slack-1")
    db_session.add(platform_connection)
    db_session.flush()

    department = Department(
        team_name="finance",
        lead_name="Alice",
        lead_email="alice@company.com",
        platform_connection_id=platform_connection.id,
        channel_ref="chan-finance",
    )
    db_session.add(department)
    db_session.commit()

    assert department.requires_verification is True


def test_department_team_name_must_be_unique(db_session):
    platform_connection = PlatformConnection(platform="slack", connection_id="conn-slack-1")
    db_session.add(platform_connection)
    db_session.flush()

    db_session.add(Department(
        team_name="finance", lead_name="Alice", lead_email="alice@company.com",
        platform_connection_id=platform_connection.id, channel_ref="chan-finance",
    ))
    db_session.commit()

    db_session.add(Department(
        team_name="finance", lead_name="Bob", lead_email="bob@company.com",
        platform_connection_id=platform_connection.id, channel_ref="chan-finance-2",
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_pending_verification_unique_per_sender_and_channel(db_session):
    platform_connection = PlatformConnection(platform="slack", connection_id="conn-slack-1")
    db_session.add(platform_connection)
    db_session.flush()
    department = Department(
        team_name="finance", lead_name="Alice", lead_email="alice@company.com",
        platform_connection_id=platform_connection.id, channel_ref="chan-finance",
    )
    db_session.add(department)
    db_session.flush()

    db_session.add(PendingVerification(
        sender_handle="U123", channel="slack",
        target_department_id=department.id, message_text="what's the Q1 report?",
    ))
    db_session.commit()

    db_session.add(PendingVerification(
        sender_handle="U123", channel="slack",
        target_department_id=department.id, message_text="a different question",
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
