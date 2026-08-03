from types import SimpleNamespace

from app.ingest.handler import build_on_message_handler
from app.models.message import Message
from app.models.person import PersonEntity


def _fake_message(**overrides):
    defaults = dict(
        id="msg-100",
        channel="email",
        agent_id="support",
        thread_id=None,
        sender={"address": "customer@example.com"},
        text="Hello",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_handles_new_message_end_to_end(session_factory):
    handle = build_on_message_handler(session_factory)
    handle(_fake_message())

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-100").one()
        assert message.agent_id == "support"
        assert message.channel == "email"

        person = db.query(PersonEntity).one()
        assert person.is_provisional is True
    finally:
        db.close()


def test_duplicate_message_is_not_persisted_twice(session_factory):
    handle = build_on_message_handler(session_factory)
    handle(_fake_message(id="msg-101"))
    handle(_fake_message(id="msg-101"))

    db = session_factory()
    try:
        count = db.query(Message).filter_by(caspian_message_id="msg-101").count()
        assert count == 1
    finally:
        db.close()


def test_known_sender_reuses_person_entity(session_factory):
    handle = build_on_message_handler(session_factory)
    handle(_fake_message(id="msg-102", sender={"address": "same@example.com"}))
    handle(_fake_message(id="msg-103", sender={"address": "same@example.com"}))

    db = session_factory()
    try:
        assert db.query(PersonEntity).count() == 1
    finally:
        db.close()


def test_message_missing_required_fields_is_dropped(session_factory):
    handle = build_on_message_handler(session_factory)
    handle(_fake_message(id="msg-104", sender={}))

    db = session_factory()
    try:
        assert db.query(Message).filter_by(caspian_message_id="msg-104").count() == 0
    finally:
        db.close()
