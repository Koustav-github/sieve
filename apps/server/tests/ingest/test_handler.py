import logging
from types import SimpleNamespace

from caspian_sdk import Message as CaspianMessage
from sqlalchemy.exc import IntegrityError

from app.ingest.handler import build_on_message_handler
from app.models.department import Department
from app.models.message import Message
from app.models.person import PersonEntity
from app.models.platform_connection import PlatformConnection
from app.relay.schemas import RelayExtractionResult

RELAY_SENDER_CONNECTION_ID = "conn-relay-sender"


class _StubRelayLLM:
    """No-op stand-in: always says 'not a relay request', so existing
    ingestion tests that don't care about relay aren't forced to exercise
    dispatch/auth."""

    def invoke(self, prompt):
        return RelayExtractionResult(is_relay_request=False)


STUB_RELAY_LLM = _StubRelayLLM()


class _FakeClient:
    def initiate(self, connection_id, recipient, text):
        return {"conversation_id": "conv-stub"}

    def send_message(self, conversation_id, text=None, **kwargs):
        return {"id": "sent-stub"}

    def reply(self, message_id, text=None, **kwargs):
        return {"id": "reply-stub"}


FAKE_CLIENT = _FakeClient()


class _SyncExecutor:
    """Runs `submit()`'d work immediately, inline, on the caller's thread -
    keeps tests deterministic without waiting on a real thread pool."""

    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


SYNC_EXECUTOR = _SyncExecutor()


def _fake_message(**overrides):
    defaults = dict(
        id="msg-100",
        channel="slack",
        connection_id="conn-personal-1",
        conversation_id=None,
        sender={"address": "U-1"},
        text="Hello",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_handler(session_factory, relay_llm=STUB_RELAY_LLM, executor=SYNC_EXECUTOR, client=FAKE_CLIENT):
    return build_on_message_handler(session_factory, relay_llm, executor, client, RELAY_SENDER_CONNECTION_ID)


def test_handles_new_message_end_to_end(session_factory):
    handle = _build_handler(session_factory)
    handle(_fake_message())

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-100").one()
        assert message.agent_id == "personal"
        assert message.channel == "slack"

        person = db.query(PersonEntity).one()
        assert person.is_provisional is True
    finally:
        db.close()


def test_duplicate_message_is_not_persisted_twice(session_factory):
    handle = _build_handler(session_factory)
    handle(_fake_message(id="msg-101"))
    handle(_fake_message(id="msg-101"))

    db = session_factory()
    try:
        assert db.query(Message).filter_by(caspian_message_id="msg-101").count() == 1
    finally:
        db.close()


def test_known_sender_reuses_person_entity(session_factory):
    handle = _build_handler(session_factory)
    handle(_fake_message(id="msg-102", sender={"address": "same-user"}))
    handle(_fake_message(id="msg-103", sender={"address": "same-user"}))

    db = session_factory()
    try:
        assert db.query(PersonEntity).count() == 1
    finally:
        db.close()


def test_message_missing_required_fields_is_dropped(session_factory):
    handle = _build_handler(session_factory)
    handle(_fake_message(id="msg-104", sender={}))

    db = session_factory()
    try:
        assert db.query(Message).filter_by(caspian_message_id="msg-104").count() == 0
    finally:
        db.close()


def test_handler_catches_and_logs_exception_without_propagating(session_factory, monkeypatch):
    import app.ingest.handler as handler_module

    def failing_persist_message(*args, **kwargs):
        raise RuntimeError("Database failure")

    monkeypatch.setattr(handler_module, "persist_message", failing_persist_message)

    handle = _build_handler(session_factory)
    handle(_fake_message(id="msg-105"))

    db = session_factory()
    try:
        assert db.query(Message).filter_by(caspian_message_id="msg-105").count() == 0
    finally:
        db.close()


def test_handler_treats_integrity_error_as_expected_race(session_factory, monkeypatch, caplog):
    import app.ingest.handler as handler_module

    def failing_persist_message(*args, **kwargs):
        raise IntegrityError("insert into messages ...", {}, Exception("unique constraint"))

    monkeypatch.setattr(handler_module, "persist_message", failing_persist_message)

    handle = _build_handler(session_factory)
    with caplog.at_level(logging.WARNING, logger="app.ingest.handler"):
        handle(_fake_message(id="msg-106"))

    db = session_factory()
    try:
        assert db.query(Message).filter_by(caspian_message_id="msg-106").count() == 0
    finally:
        db.close()

    handler_records = [r for r in caplog.records if r.name == "app.ingest.handler"]
    assert any(r.levelno == logging.WARNING for r in handler_records)
    assert not any(r.levelno >= logging.ERROR for r in handler_records)


def test_group_chat_message_stamps_department_team_name(session_factory, db_session):
    platform_connection = PlatformConnection(platform="slack", connection_id="conn-finance-1")
    db_session.add(platform_connection)
    db_session.flush()
    db_session.add(Department(
        team_name="finance", lead_name="Lead", lead_email="finance@company.com",
        platform_connection_id=platform_connection.id, channel_ref="chan-finance",
    ))
    db_session.commit()

    handle = _build_handler(session_factory)
    handle(_fake_message(id="msg-200", connection_id="conn-finance-1", conversation_id="chan-finance"))

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-200").one()
        assert message.agent_id == "finance"
    finally:
        db.close()


def test_raw_payload_includes_full_real_message_fields(session_factory):
    handle = _build_handler(session_factory)
    caspian_message = CaspianMessage(
        id="msg-201",
        conversation_id=None,
        connection_id="conn-personal-1",
        customer_id="cust-201",
        agent_id="whatever",
        channel="slack",
        sender={"address": "U-2"},
        subject="Need help",
        text="body text",
        html="<p>body text</p>",
        _client=None,
        media=[{"url": "https://example.com/f.png", "mime_type": "image/png"}],
    )

    handle(caspian_message)

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-201").one()
        payload = message.raw_payload
        assert payload["subject"] == "Need help"
        assert payload["html"] == "<p>body text</p>"
        assert "_client" not in payload
    finally:
        db.close()


def test_handler_dispatches_relay_asynchronously(session_factory):
    """Relay must run off the ingest listen() loop: handle() should return
    (and the message should already be durably persisted) before a slow
    relay LLM call finishes."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    started = threading.Event()
    finished = threading.Event()

    class _SlowLLM:
        def invoke(self, prompt):
            started.set()
            time.sleep(0.3)
            finished.set()
            return RelayExtractionResult(is_relay_request=False)

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        handle = _build_handler(session_factory, relay_llm=_SlowLLM(), executor=executor)
        handle(_fake_message(id="msg-400"))

        assert not finished.is_set()

        db = session_factory()
        try:
            assert db.query(Message).filter_by(caspian_message_id="msg-400").count() == 1
        finally:
            db.close()

        assert started.wait(timeout=2), "relay pipeline never started"
        assert finished.wait(timeout=2), "relay pipeline never finished"
    finally:
        executor.shutdown(wait=True)
