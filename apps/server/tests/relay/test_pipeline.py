from app.ingest.message_store import persist_message
from app.models.employee import Employee
from app.models.person import PersonEntity
from app.models.relay_request import RelayRequest
from app.relay.pipeline import (
    DISPATCH_FAILURE_REPLY_TEXT,
    _caspian_message_id,
    _safe_deliver_reply,
    run_relay,
)
from app.relay.schemas import RelayExtractionResult


class _FakeLLM:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        return self.result


class _RaisingLLM:
    def invoke(self, prompt):
        raise RuntimeError("LLM blew up")


class _FakeClient:
    def __init__(self, initiate_response=None, initiate_raises=None):
        self.initiate_calls = []
        self.reply_calls = []
        self._initiate_response = initiate_response or {"conversation_id": "conv-out-1"}
        self._initiate_raises = initiate_raises

    def initiate(self, connection_id, recipient, text):
        self.initiate_calls.append((connection_id, recipient, text))
        if self._initiate_raises:
            raise self._initiate_raises
        return self._initiate_response

    def reply(self, message_id, text=None, **kwargs):
        self.reply_calls.append((message_id, text))
        return {"id": "reply-ok"}


IDENTITY_EMAIL_CONNECTIONS = {
    "careers": {"id": "conn-careers", "address": "careers@sieve.test"},
    "support": {"id": "conn-support", "address": "support@sieve.test"},
    "internal": {"id": "conn-internal", "address": "internal@sieve.test"},
}


def _persist_source_message(db_session, caspian_message_id="msg-src"):
    message = persist_message(
        db_session,
        caspian_message_id=caspian_message_id,
        agent_id="internal",
        channel="slack",
        sender_handle="U-1",
        thread_id="thread-1",
        raw_payload={},
    )
    db_session.commit()
    return message


def test_non_relay_message_does_nothing(db_session):
    message = _persist_source_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=False))
    client = _FakeClient()

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-1", conversation_id="thread-1", subject=None, text="just chatting",
    )

    assert db_session.query(RelayRequest).count() == 0
    assert client.initiate_calls == []
    assert client.reply_calls == []


def test_target_support_dispatches_without_verification(db_session):
    message = _persist_source_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="support", message_text="need help",
    ))
    client = _FakeClient(initiate_response={"conversation_id": "conv-support-1"})

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-1", conversation_id="thread-1", subject=None, text="need help",
    )

    relay_request = db_session.query(RelayRequest).one()
    assert relay_request.target_identity == "support"
    assert relay_request.target_conversation_id == "conv-support-1"
    assert relay_request.status == "pending"
    assert client.initiate_calls == [("conn-internal", "support@sieve.test", "need help")]
    assert client.reply_calls == []


def test_already_verified_employee_skips_reasking(db_session):
    message = _persist_source_message(db_session, caspian_message_id="msg-src-2")
    person = PersonEntity(display_name=None, is_provisional=True, verified_employee=True)
    db_session.add(person)
    db_session.flush()
    from app.models.channel_handle import ChannelHandle
    db_session.add(ChannelHandle(person_entity_id=person.id, channel="slack", handle="U-2"))
    db_session.commit()

    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="internal", message_text="need sign-off",
        claims_employee=False, employment_id=None,
    ))
    client = _FakeClient(initiate_response={"conversation_id": "conv-internal-1"})

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="careers", channel="slack",
        sender_handle="U-2", conversation_id="thread-1", subject=None, text="need sign-off",
    )

    relay_request = db_session.query(RelayRequest).one()
    assert relay_request.target_identity == "internal"
    assert client.reply_calls == []


def test_valid_employment_id_verifies_and_dispatches(db_session):
    message = _persist_source_message(db_session, caspian_message_id="msg-src-3")
    db_session.add(Employee(employment_id="EMP-1", name="Alice"))
    db_session.commit()

    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="careers", message_text="referral question",
        claims_employee=True, employment_id="EMP-1",
    ))
    client = _FakeClient(initiate_response={"conversation_id": "conv-careers-1"})

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-3", conversation_id="thread-1", subject=None, text="referral question",
    )

    relay_request = db_session.query(RelayRequest).one()
    assert relay_request.target_identity == "careers"
    person = db_session.query(PersonEntity).filter_by(is_provisional=True).one()
    assert person.verified_employee is True
    assert client.reply_calls == []


def test_invalid_employment_id_replies_and_falls_back_to_support(db_session):
    message = _persist_source_message(db_session, caspian_message_id="msg-src-4")

    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="internal", message_text="need sign-off",
        claims_employee=True, employment_id="does-not-exist",
    ))
    client = _FakeClient(initiate_response={"conversation_id": "conv-fallback-1"})

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-4", conversation_id="thread-1", subject=None, text="need sign-off",
    )

    relay_request = db_session.query(RelayRequest).one()
    assert relay_request.target_identity == "support"
    assert len(client.reply_calls) == 1
    assert client.reply_calls[0][0] == "msg-src-4"


def test_no_employment_claim_replies_and_falls_back_to_support(db_session):
    message = _persist_source_message(db_session, caspian_message_id="msg-src-5")

    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="internal", message_text="need sign-off",
        claims_employee=False, employment_id=None,
    ))
    client = _FakeClient(initiate_response={"conversation_id": "conv-fallback-2"})

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-5", conversation_id="thread-1", subject=None, text="need sign-off",
    )

    relay_request = db_session.query(RelayRequest).one()
    assert relay_request.target_identity == "support"
    assert len(client.reply_calls) == 1


def test_dispatch_failure_replies_with_error_and_creates_no_relay_request(db_session):
    message = _persist_source_message(db_session, caspian_message_id="msg-src-6")

    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="support", message_text="need help",
    ))
    client = _FakeClient(initiate_raises=RuntimeError("gateway unreachable"))

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-6", conversation_id="thread-1", subject=None, text="need help",
    )

    assert db_session.query(RelayRequest).count() == 0
    assert len(client.reply_calls) == 1
    assert client.reply_calls[0][0] == "msg-src-6"


def test_employment_id_lookup_db_error_replies_and_falls_back_to_support(db_session, monkeypatch):
    message = _persist_source_message(db_session, caspian_message_id="msg-src-8")

    def failing_verify(db, employment_id):
        raise RuntimeError("DB connection lost")

    import app.relay.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "verify_employment_id", failing_verify)

    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="internal", message_text="need sign-off",
        claims_employee=True, employment_id="EMP-1",
    ))
    client = _FakeClient(initiate_response={"conversation_id": "conv-fallback-3"})

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-8", conversation_id="thread-1", subject=None, text="need sign-off",
    )

    relay_request = db_session.query(RelayRequest).one()
    assert relay_request.target_identity == "support"
    assert len(client.reply_calls) == 1


def test_reply_delivery_failure_leaves_request_pending(db_session):
    # The reply to a relay we sent out lands back on the SOURCE identity's
    # own connection (see run_relay's docstring / Finding 1): the message
    # arrives with agent_identity == the relay's source_identity
    # ("internal"), sent by whoever holds the target identity's own address
    # ("support@sieve.test").
    original_message = _persist_source_message(db_session, caspian_message_id="msg-original-2")
    reply_message = persist_message(
        db_session,
        caspian_message_id="msg-reply-2",
        agent_id="internal",
        channel="email",
        sender_handle="support@sieve.test",
        thread_id="conv-pending-2",
        raw_payload={},
    )
    db_session.add(RelayRequest(
        source_message_id=original_message.id,
        source_identity="internal",
        target_identity="support",
        target_conversation_id="conv-pending-2",
        message_text="need help",
        status="pending",
    ))
    db_session.commit()

    class _ReplyRaisingClient(_FakeClient):
        def reply(self, message_id, text=None, **kwargs):
            raise RuntimeError("channel unreachable")

    client = _ReplyRaisingClient()
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=False))

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=reply_message.id, agent_identity="internal", channel="email",
        sender_handle="support@sieve.test", conversation_id="conv-pending-2",
        subject=None, text="here's the answer",
    )

    relay_request = db_session.query(RelayRequest).one()
    assert relay_request.status == "pending"
    assert relay_request.completed_at is None


def test_reply_correlation_mismatched_agent_identity_does_not_complete(db_session):
    # Same target_conversation_id as a pending relay, but arriving on a
    # DIFFERENT identity's connection than the one that sent the relay out
    # (source_identity="internal", but this message's agent_identity is
    # "careers"). This must not be treated as the awaited reply.
    original_message = _persist_source_message(db_session, caspian_message_id="msg-original-3")
    unrelated_message = persist_message(
        db_session,
        caspian_message_id="msg-unrelated-3",
        agent_id="careers",
        channel="email",
        sender_handle="someone@sieve.test",
        thread_id="conv-pending-3",
        raw_payload={},
    )
    db_session.add(RelayRequest(
        source_message_id=original_message.id,
        source_identity="internal",
        target_identity="support",
        target_conversation_id="conv-pending-3",
        message_text="need help",
        status="pending",
    ))
    db_session.commit()

    client = _FakeClient()
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=False))

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=unrelated_message.id, agent_identity="careers", channel="email",
        sender_handle="someone@sieve.test", conversation_id="conv-pending-3",
        subject=None, text="unrelated message",
    )

    relay_request = db_session.query(RelayRequest).one()
    assert relay_request.status == "pending"
    assert relay_request.completed_at is None
    assert client.reply_calls == []


def test_relay_detection_llm_failure_is_soft_failed(db_session):
    message = _persist_source_message(db_session, caspian_message_id="msg-src-7")
    client = _FakeClient()

    run_relay(
        _RaisingLLM(), client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-7", conversation_id="thread-1", subject=None, text="hello",
    )

    assert db_session.query(RelayRequest).count() == 0
    assert client.initiate_calls == []
    assert client.reply_calls == []


def test_reply_correlation_delivers_reply_and_completes_request(db_session):
    # The reply to a relay we sent out lands back on the SOURCE identity's
    # own connection (see run_relay's docstring / Finding 1): send_relay()
    # cold-starts the outbound conversation FROM the source identity's
    # connection TO the target's address, so standard reply-threading
    # routes the reply back to the source identity ("internal"), sent by
    # whoever holds the target's own address ("support@sieve.test").
    original_message = _persist_source_message(db_session, caspian_message_id="msg-original")
    reply_message = persist_message(
        db_session,
        caspian_message_id="msg-reply",
        agent_id="internal",
        channel="email",
        sender_handle="support@sieve.test",
        thread_id="conv-pending-1",
        raw_payload={},
    )
    db_session.add(RelayRequest(
        source_message_id=original_message.id,
        source_identity="internal",
        target_identity="support",
        target_conversation_id="conv-pending-1",
        message_text="need help",
        status="pending",
    ))
    db_session.commit()

    client = _FakeClient()
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=False))

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=reply_message.id, agent_identity="internal", channel="email",
        sender_handle="support@sieve.test", conversation_id="conv-pending-1",
        subject=None, text="here's the answer",
    )

    relay_request = db_session.query(RelayRequest).one()
    assert relay_request.status == "completed"
    assert relay_request.completed_at is not None
    assert client.reply_calls == [("msg-original", "here's the answer")]
    assert client.initiate_calls == []


def test_missing_identity_connection_replies_with_error_and_creates_no_relay_request(db_session):
    message = _persist_source_message(db_session, caspian_message_id="msg-src-10")
    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="support", message_text="need help",
    ))
    client = _FakeClient()
    connections_missing_target = {
        "internal": {"id": "conn-internal", "address": "internal@sieve.test"},
        # "support" deliberately absent - simulates register_identities()
        # not having a connection for the resolved target identity.
    }

    run_relay(
        llm, client, db_session, connections_missing_target,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-10", conversation_id="thread-1", subject=None, text="need help",
    )

    assert db_session.query(RelayRequest).count() == 0
    assert client.initiate_calls == []
    assert client.reply_calls == [("msg-src-10", DISPATCH_FAILURE_REPLY_TEXT)]


def test_unexpected_error_is_swallowed_by_top_level_safety_net(db_session, monkeypatch):
    message = _persist_source_message(db_session, caspian_message_id="msg-src-11")
    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="support", message_text="need help",
    ))
    client = _FakeClient()

    import app.relay.pipeline as pipeline_module

    def failing_resolve_sender(db, *, channel, handle):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(pipeline_module, "resolve_sender", failing_resolve_sender)

    # Must not raise, per run_relay's "never raises" contract, even though
    # this error occurs after the LLM call's own try/except and outside of
    # _dispatch's try/except.
    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-11", conversation_id="thread-1", subject=None, text="need help",
    )

    assert db_session.query(RelayRequest).count() == 0
    assert client.initiate_calls == []


def test_dispatch_db_commit_failure_after_successful_send_replies_and_rolls_back(
    db_session, monkeypatch
):
    # send_relay() succeeds (message is already on the wire to the target)
    # but recording the RelayRequest fails on commit - e.g. an IntegrityError
    # from the target_conversation_id UniqueConstraint. The requester must
    # still get a DISPATCH_FAILURE_REPLY_TEXT reply, and no RelayRequest row
    # should survive.
    message = _persist_source_message(db_session, caspian_message_id="msg-src-12")

    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="support", message_text="need help",
    ))
    client = _FakeClient(initiate_response={"conversation_id": "conv-commit-fail"})

    def failing_commit():
        raise RuntimeError("IntegrityError: duplicate target_conversation_id")

    monkeypatch.setattr(db_session, "commit", failing_commit)

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="internal", channel="slack",
        sender_handle="U-12", conversation_id="thread-1", subject=None, text="need help",
    )

    assert client.initiate_calls == [("conn-internal", "support@sieve.test", "need help")]
    assert db_session.query(RelayRequest).count() == 0
    assert client.reply_calls == [("msg-src-12", DISPATCH_FAILURE_REPLY_TEXT)]


def test_self_relay_target_equals_agent_identity_replies_without_dispatch(db_session):
    # A customer emailing support@ asking "please pass this to support"
    # would extract target_identity="support" while agent_identity is
    # already "support". This must be refused before dispatch, not sent out
    # and looped back to itself as a fake "reply".
    message = _persist_source_message(db_session, caspian_message_id="msg-src-13")

    llm = _FakeLLM(RelayExtractionResult(
        is_relay_request=True, target_identity="support",
        message_text="please forward this to support",
    ))
    client = _FakeClient()

    run_relay(
        llm, client, db_session, IDENTITY_EMAIL_CONNECTIONS,
        message_id=message.id, agent_identity="support", channel="email",
        sender_handle="someone@customer.test", conversation_id="thread-1",
        subject=None, text="please forward this to support",
    )

    assert db_session.query(RelayRequest).count() == 0
    assert client.initiate_calls == []
    assert len(client.reply_calls) == 1
    assert client.reply_calls[0][0] == "msg-src-13"


def test_caspian_message_id_returns_none_for_missing_message(db_session):
    assert _caspian_message_id(db_session, 999_999) is None


def test_safe_deliver_reply_skips_and_returns_false_when_message_missing(db_session):
    client = _FakeClient()

    delivered = _safe_deliver_reply(client, db_session, 999_999, "hello")

    assert delivered is False
    assert client.reply_calls == []
