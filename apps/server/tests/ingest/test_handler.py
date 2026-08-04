import logging
from types import SimpleNamespace

from caspian_sdk import Message as CaspianMessage
from sqlalchemy.exc import IntegrityError

from app.ingest.handler import build_on_message_handler
from app.models.message import Message
from app.models.person import PersonEntity

CONNECTION_IDENTITIES = {"conn-support": "support", "conn-200": "support"}


class _StubClassificationGraph:
    """No-op stand-in: returns a fully-decided state without touching the DB
    or any LLM, so existing ingestion tests that don't care about
    classification aren't forced to seed buckets/rules."""

    def invoke(self, state):
        return {
            **state,
            "bucket_id": None,
            "bucket_name": None,
            "deciding_layer": "L1",
            "confidence": None,
            "reason": "stub graph - classification not exercised in this test",
            "subject_raw_text": None,
            "subject_person_entity_id": None,
        }


STUB_GRAPH = _StubClassificationGraph()


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


def test_handles_new_message_end_to_end(session_factory):
    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, STUB_GRAPH, SYNC_EXECUTOR)
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
    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, STUB_GRAPH, SYNC_EXECUTOR)
    handle(_fake_message(id="msg-101"))
    handle(_fake_message(id="msg-101"))

    db = session_factory()
    try:
        count = db.query(Message).filter_by(caspian_message_id="msg-101").count()
        assert count == 1
    finally:
        db.close()


def test_known_sender_reuses_person_entity(session_factory):
    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, STUB_GRAPH, SYNC_EXECUTOR)
    handle(_fake_message(id="msg-102", sender={"address": "same@example.com"}))
    handle(_fake_message(id="msg-103", sender={"address": "same@example.com"}))

    db = session_factory()
    try:
        assert db.query(PersonEntity).count() == 1
    finally:
        db.close()


def test_message_missing_required_fields_is_dropped(session_factory):
    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, STUB_GRAPH, SYNC_EXECUTOR)
    handle(_fake_message(id="msg-104", sender={}))

    db = session_factory()
    try:
        assert db.query(Message).filter_by(caspian_message_id="msg-104").count() == 0
    finally:
        db.close()


def test_handler_catches_and_logs_exception_without_propagating(session_factory, monkeypatch):
    import app.ingest.handler as handler_module

    # Monkeypatch persist_message to raise an exception
    def failing_persist_message(*args, **kwargs):
        raise RuntimeError("Database failure")

    monkeypatch.setattr(handler_module, "persist_message", failing_persist_message)

    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, STUB_GRAPH, SYNC_EXECUTOR)
    # This should NOT raise, even though persist_message fails
    handle(_fake_message(id="msg-105"))

    # Verify the message was NOT persisted (rolled back)
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

    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, STUB_GRAPH, SYNC_EXECUTOR)
    with caplog.at_level(logging.WARNING, logger="app.ingest.handler"):
        handle(_fake_message(id="msg-106"))

    db = session_factory()
    try:
        assert db.query(Message).filter_by(caspian_message_id="msg-106").count() == 0
    finally:
        db.close()

    # Logged as a warning (dedup/handle race), not via logger.exception at
    # ERROR level with a traceback like a real, unexpected failure would be.
    handler_records = [r for r in caplog.records if r.name == "app.ingest.handler"]
    assert any(r.levelno == logging.WARNING for r in handler_records)
    assert not any(r.levelno >= logging.ERROR for r in handler_records)


def test_thread_id_reads_conversation_id_primary(session_factory):
    """I1: the real caspian_sdk.Message has `conversation_id`, not `thread_id`."""
    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, STUB_GRAPH, SYNC_EXECUTOR)
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
    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, STUB_GRAPH, SYNC_EXECUTOR)
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
    `subject` in particular is the primary bucketing signal for classification."""
    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, STUB_GRAPH, SYNC_EXECUTOR)
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
        # Not serializable / not useful downstream - must be dropped.
        assert "_client" not in payload
    finally:
        db.close()


def test_raw_payload_falls_back_to_vars_for_non_dataclass_fake(session_factory):
    """I2: the fallback path must still work for the simple SimpleNamespace
    fakes used elsewhere in this file, which aren't real dataclasses."""
    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, STUB_GRAPH, SYNC_EXECUTOR)
    handle(_fake_message(id="msg-201"))

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-201").one()
        assert message.raw_payload["text"] == "Hello"
        assert message.raw_payload["channel"] == "email"
    finally:
        db.close()


def test_handler_invokes_classification_and_persists_routing_decision(session_factory, db_session):
    from app.classify.graph import build_classification_graph
    from app.classify.schemas import L3ClassificationResult, SubjectExtractionResult
    from app.models.bucket import Bucket
    from app.models.routing_decision import RoutingDecision

    bucket = Bucket(name="customer_support", description="support", is_active=True)
    db_session.add(bucket)
    db_session.commit()

    class _FakeLLM:
        def __init__(self, result):
            self.result = result

        def invoke(self, prompt):
            return self.result

    graph = build_classification_graph(
        session_factory,
        _FakeLLM(L3ClassificationResult(bucket_name="customer_support", reason="matches", confidence=0.6)),
        _FakeLLM(SubjectExtractionResult(subject_name=None, reason="n/a")),
    )

    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, graph, SYNC_EXECUTOR)
    handle(_fake_message(id="msg-300"))

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-300").one()
        decision = db.query(RoutingDecision).filter_by(message_id=message.id).one()
        assert decision.bucket_id == bucket.id
        assert decision.deciding_layer == "L3"
    finally:
        db.close()


def test_handler_survives_classification_failure_message_still_persisted(session_factory, db_session):
    class _RaisingGraph:
        def invoke(self, state):
            raise RuntimeError("graph blew up")

    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES, _RaisingGraph(), SYNC_EXECUTOR)
    handle(_fake_message(id="msg-301"))

    db = session_factory()
    try:
        assert db.query(Message).filter_by(caspian_message_id="msg-301").count() == 1
    finally:
        db.close()


def test_handler_dispatches_classification_asynchronously(session_factory):
    """Classification must run off the ingest listen() loop: handle() should
    return (and the message should already be durably persisted) before a
    slow classification graph finishes, proving the two are not serialized
    on the same thread."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    started = threading.Event()
    finished = threading.Event()

    class _SlowGraph:
        def invoke(self, state):
            started.set()
            time.sleep(0.3)
            finished.set()
            return {
                **state,
                "bucket_id": None,
                "bucket_name": None,
                "deciding_layer": "L1",
                "confidence": None,
                "reason": "slow",
                "subject_raw_text": None,
                "subject_person_entity_id": None,
            }

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        handle = build_on_message_handler(
            session_factory, CONNECTION_IDENTITIES, _SlowGraph(), executor
        )
        handle(_fake_message(id="msg-400"))

        # handle() returned without waiting for the slow graph to finish.
        assert not finished.is_set()

        db = session_factory()
        try:
            assert db.query(Message).filter_by(caspian_message_id="msg-400").count() == 1
        finally:
            db.close()

        assert started.wait(timeout=2), "classification never started"
        assert finished.wait(timeout=2), "classification never finished"
    finally:
        executor.shutdown(wait=True)
