import logging
from types import SimpleNamespace

from caspian_sdk import Message as CaspianMessage
from sqlalchemy.exc import IntegrityError

from app.ingest.handler import build_on_message_handler
from app.models.message import Message
from app.models.person import PersonEntity
from app.models.relay_request import RelayRequest
from app.relay.schemas import RelayExtractionResult

CONNECTION_IDENTITIES = {"conn-support": "support", "conn-200": "support"}

IDENTITY_EMAIL_CONNECTIONS = {
    "careers": {"id": "conn-careers", "address": "careers@sieve.test"},
    "support": {"id": "conn-support", "address": "support@sieve.test"},
    "internal": {"id": "conn-internal", "address": "internal@sieve.test"},
}


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
        channel="email",
        connection_id="conn-support",
        conversation_id=None,
        sender={"address": "customer@example.com"},
        text="Hello",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_handler(
    session_factory, relay_llm=STUB_RELAY_LLM, executor=SYNC_EXECUTOR, client=FAKE_CLIENT,
    identity_email_connections=None,
):
    return build_on_message_handler(
        session_factory,
        CONNECTION_IDENTITIES,
        relay_llm,
        executor,
        client,
        identity_email_connections if identity_email_connections is not None else IDENTITY_EMAIL_CONNECTIONS,
    )


def test_handles_new_message_end_to_end(session_factory):
    handle = _build_handler(session_factory)
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
    handle = _build_handler(session_factory)
    handle(_fake_message(id="msg-101"))
    handle(_fake_message(id="msg-101"))

    db = session_factory()
    try:
        count = db.query(Message).filter_by(caspian_message_id="msg-101").count()
        assert count == 1
    finally:
        db.close()


def test_known_sender_reuses_person_entity(session_factory):
    handle = _build_handler(session_factory)
    handle(_fake_message(id="msg-102", sender={"address": "same@example.com"}))
    handle(_fake_message(id="msg-103", sender={"address": "same@example.com"}))

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
    """I4: an IntegrityError (e.g. a concurrent-sender race on the
    channel_handles unique constraint) must be caught distinctly from a real
    failure, logged at a lower severity (no traceback), and not propagate."""
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


def test_thread_id_reads_conversation_id_primary(session_factory):
    """I1: the real caspian_sdk.Message has `conversation_id`, not `thread_id`."""
    handle = _build_handler(session_factory)
    handle(_fake_message(id="msg-107", conversation_id="conv-77"))

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-107").one()
        assert message.thread_id == "conv-77"
    finally:
        db.close()


def test_thread_id_falls_back_to_thread_id_attr(session_factory):
    """I1: `thread_id` remains a fallback for robustness against simpler fakes
    that don't set `conversation_id` at all."""
    handle = _build_handler(session_factory)
    handle(_fake_message(id="msg-108", thread_id="legacy-thread-9"))

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-108").one()
        assert message.thread_id == "legacy-thread-9"
    finally:
        db.close()


def test_raw_payload_includes_full_real_message_fields(session_factory):
    """I2: raw_payload must be built from the real caspian_sdk.Message dataclass
    fields (subject, html, media, ids, ...), not a hand-picked subset -
    `subject` in particular is a primary signal for relay-request detection."""
    handle = _build_handler(session_factory)
    caspian_message = CaspianMessage(
        id="msg-200",
        conversation_id="conv-200",
        connection_id="conn-200",
        customer_id="cust-200",
        agent_id="support",
        channel="email",
        sender={"address": "customer@example.com"},
        subject="Need help with my order",
        text="body text",
        html="<p>body text</p>",
        _client=None,
        media=[{"url": "https://example.com/f.png", "mime_type": "image/png"}],
    )

    handle(caspian_message)

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-200").one()
        assert message.thread_id == "conv-200"
        payload = message.raw_payload
        assert payload["subject"] == "Need help with my order"
        assert payload["html"] == "<p>body text</p>"
        assert payload["media"] == [
            {"url": "https://example.com/f.png", "mime_type": "image/png"}
        ]
        assert payload["conversation_id"] == "conv-200"
        assert payload["connection_id"] == "conn-200"
        assert payload["customer_id"] == "cust-200"
        assert "_client" not in payload
    finally:
        db.close()


def test_raw_payload_falls_back_to_vars_for_non_dataclass_fake(session_factory):
    """I2: the fallback path must still work for the simple SimpleNamespace
    fakes used elsewhere in this file, which aren't real dataclasses."""
    handle = _build_handler(session_factory)
    handle(_fake_message(id="msg-201"))

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-201").one()
        assert message.raw_payload["text"] == "Hello"
        assert message.raw_payload["channel"] == "email"
    finally:
        db.close()


def test_handler_invokes_relay_and_persists_relay_request(session_factory):
    class _RelayLLM:
        def invoke(self, prompt):
            return RelayExtractionResult(
                is_relay_request=True,
                target_identity="support",
                message_text="please help with my order",
                claims_employee=False,
                employment_id=None,
            )

    handle = _build_handler(session_factory, relay_llm=_RelayLLM())
    handle(_fake_message(id="msg-300"))

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-300").one()
        relay_request = db.query(RelayRequest).filter_by(source_message_id=message.id).one()
        assert relay_request.target_identity == "support"
        assert relay_request.status == "pending"
    finally:
        db.close()


def test_handler_survives_relay_failure_message_still_persisted(session_factory):
    class _RaisingLLM:
        def invoke(self, prompt):
            raise RuntimeError("relay LLM blew up")

    handle = _build_handler(session_factory, relay_llm=_RaisingLLM())
    handle(_fake_message(id="msg-301"))

    db = session_factory()
    try:
        assert db.query(Message).filter_by(caspian_message_id="msg-301").count() == 1
    finally:
        db.close()


def test_handler_dispatches_relay_asynchronously(session_factory):
    """Relay must run off the ingest listen() loop: handle() should return
    (and the message should already be durably persisted) before a slow
    relay LLM call finishes, proving the two are not serialized on the same
    thread."""
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
