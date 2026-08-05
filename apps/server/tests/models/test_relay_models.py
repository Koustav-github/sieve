import pytest
from sqlalchemy.exc import IntegrityError

from app.models.employee import Employee
from app.models.message import Message
from app.models.person import PersonEntity
from app.models.relay_request import RelayRequest


def test_employee_employment_id_must_be_unique(db_session):
    db_session.add(Employee(employment_id="EMP-1", name="Alice"))
    db_session.commit()

    db_session.add(Employee(employment_id="EMP-1", name="Bob"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_relay_request_defaults_to_pending_status(db_session):
    message = Message(
        caspian_message_id="msg-1",
        agent_id="internal",
        channel="slack",
        sender_handle="U123",
        raw_payload={},
    )
    db_session.add(message)
    db_session.flush()

    relay_request = RelayRequest(
        source_message_id=message.id,
        source_identity="internal",
        target_identity="support",
        target_conversation_id="conv-1",
        message_text="please help",
    )
    db_session.add(relay_request)
    db_session.commit()

    assert relay_request.status == "pending"
    assert relay_request.completed_at is None


def test_relay_request_target_conversation_id_must_be_unique(db_session):
    message = Message(
        caspian_message_id="msg-2",
        agent_id="internal",
        channel="slack",
        sender_handle="U124",
        raw_payload={},
    )
    db_session.add(message)
    db_session.flush()

    db_session.add(RelayRequest(
        source_message_id=message.id,
        source_identity="internal",
        target_identity="support",
        target_conversation_id="conv-dup",
        message_text="first",
    ))
    db_session.commit()

    db_session.add(RelayRequest(
        source_message_id=message.id,
        source_identity="internal",
        target_identity="careers",
        target_conversation_id="conv-dup",
        message_text="second",
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_person_entity_verified_employee_defaults_false(db_session):
    person = PersonEntity(display_name="Alice", is_provisional=False)
    db_session.add(person)
    db_session.commit()

    assert person.verified_employee is False
