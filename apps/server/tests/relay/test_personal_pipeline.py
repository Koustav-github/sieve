from app.ingest.message_store import persist_message
from app.models.department import Department
from app.models.employee import Employee
from app.models.pending_verification import PendingVerification
from app.models.person import PersonEntity
from app.models.platform_connection import PlatformConnection
from app.models.relay_request import RelayRequest
from app.relay.personal_pipeline import (
    _build_personal_prompt,
    _upsert_pending_verification,
    run_personal_relay,
)
from app.relay.schemas import RelayExtractionResult

RELAY_SENDER_CONNECTION_ID = "conn-relay-sender"


class _FakeLLM:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        return self.result


class _FakeClient:
    def __init__(self, initiate_response=None):
        self.initiate_calls = []
        self.reply_calls = []
        self._initiate_response = initiate_response or {"conversation_id": "conv-out-1"}

    def initiate(self, connection_id, recipient, text):
        self.initiate_calls.append((connection_id, recipient, text))
        return self._initiate_response

    def reply(self, message_id, text=None, **kwargs):
        self.reply_calls.append((message_id, text))
        return {"id": "reply-1"}


def _make_department(db_session, *, team_name, requires_verification=True):
    platform_connection = (
        db_session.query(PlatformConnection).filter_by(platform="slack").one_or_none()
    )
    if platform_connection is None:
        platform_connection = PlatformConnection(platform="slack", connection_id="conn-dept-1")
        db_session.add(platform_connection)
        db_session.flush()
    department = Department(
        team_name=team_name, lead_name="Lead", lead_email=f"{team_name}@company.com",
        platform_connection_id=platform_connection.id, channel_ref=f"chan-{team_name}",
        requires_verification=requires_verification,
    )
    db_session.add(department)
    db_session.commit()
    return department


def _persist_message(db_session, caspian_message_id="msg-1", sender_handle="U-1"):
    message = persist_message(
        db_session, caspian_message_id=caspian_message_id, agent_id="personal",
        channel="slack", sender_handle=sender_handle, thread_id=None, raw_payload={},
    )
    db_session.commit()
    return message


def test_exempt_target_dispatches_without_verification(db_session):
    _make_department(db_session, team_name="customercare", requires_verification=False)
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="customercare", message_text="need help"))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-1", conversation_id=None, subject=None, text="need help")

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "customercare@company.com", "need help")]
    assert db_session.query(PendingVerification).count() == 0

    # Dispatch must also record a RelayRequest so a later reply on the
    # shared relay-sender connection can be correlated back to this message.
    relay_request = db_session.query(RelayRequest).one()
    assert relay_request.source_identity == "personal"
    assert relay_request.target_identity == "customercare"
    assert relay_request.status == "pending"


def test_no_id_given_asks_for_one_and_holds_the_query(db_session):
    _make_department(db_session, team_name="finance")
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="finance", message_text="Q1 numbers please", claims_employee=False, employment_id=None))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-1", conversation_id=None, subject=None, text="ask finance for Q1 numbers")

    assert client.initiate_calls == []
    assert len(client.reply_calls) == 1
    pending = db_session.query(PendingVerification).one()
    assert pending.sender_handle == "U-1"
    assert pending.message_text == "Q1 numbers please"
    assert pending.target_department.team_name == "finance"


def test_id_given_upfront_verifies_and_dispatches_immediately(db_session):
    _make_department(db_session, team_name="finance")
    db_session.add(Employee(employment_id="EMP-1", name="Alice"))
    db_session.commit()
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="finance", message_text="Q1 numbers please", claims_employee=True, employment_id="EMP-1"))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-2", conversation_id=None, subject=None, text="my id is EMP-1, ask finance for Q1 numbers")

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "finance@company.com", "Q1 numbers please")]
    person = db_session.query(PersonEntity).filter_by(is_provisional=True).one()
    assert person.verified_employee is True


def test_followup_message_with_valid_id_resumes_held_query(db_session):
    department = _make_department(db_session, team_name="finance")
    db_session.add(Employee(employment_id="EMP-2", name="Bob"))
    _persist_message(db_session, caspian_message_id="msg-original", sender_handle="U-3")
    db_session.add(PendingVerification(
        sender_handle="U-3", channel="slack",
        target_department_id=department.id, message_text="Q1 numbers please",
    ))
    db_session.commit()
    followup = _persist_message(db_session, caspian_message_id="msg-followup", sender_handle="U-3")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, employment_id="EMP-2", claims_employee=True))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=followup.id, channel="slack", sender_handle="U-3", conversation_id=None, subject=None, text="EMP-2")

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "finance@company.com", "Q1 numbers please")]
    assert db_session.query(PendingVerification).count() == 0


def test_followup_message_with_invalid_id_drops_pending_and_replies(db_session):
    department = _make_department(db_session, team_name="finance")
    _persist_message(db_session, caspian_message_id="msg-original-2", sender_handle="U-4")
    db_session.add(PendingVerification(
        sender_handle="U-4", channel="slack",
        target_department_id=department.id, message_text="Q1 numbers please",
    ))
    db_session.commit()
    followup = _persist_message(db_session, caspian_message_id="msg-followup-2", sender_handle="U-4")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, employment_id="does-not-exist", claims_employee=True))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=followup.id, channel="slack", sender_handle="U-4", conversation_id=None, subject=None, text="does-not-exist")

    assert client.initiate_calls == []
    assert len(client.reply_calls) == 1
    assert db_session.query(PendingVerification).count() == 0


def test_new_query_replaces_older_pending_verification(db_session):
    department = _make_department(db_session, team_name="finance")
    _persist_message(db_session, caspian_message_id="msg-first", sender_handle="U-5")
    db_session.add(PendingVerification(
        sender_handle="U-5", channel="slack",
        target_department_id=department.id, message_text="old question",
    ))
    db_session.commit()
    new_message = _persist_message(db_session, caspian_message_id="msg-second", sender_handle="U-5")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="finance", message_text="new question", claims_employee=False, employment_id=None))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=new_message.id, channel="slack", sender_handle="U-5", conversation_id=None, subject=None, text="actually ask finance a new question")

    pending = db_session.query(PendingVerification).one()
    assert pending.message_text == "new question"


def test_already_verified_employee_skips_reasking(db_session):
    _make_department(db_session, team_name="finance")
    from app.models.channel_handle import ChannelHandle
    person = PersonEntity(display_name=None, is_provisional=True, verified_employee=True)
    db_session.add(person)
    db_session.flush()
    db_session.add(ChannelHandle(person_entity_id=person.id, channel="slack", handle="U-6"))
    db_session.commit()
    message = _persist_message(db_session, sender_handle="U-6")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="finance", message_text="Q1 numbers please", claims_employee=False, employment_id=None))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-6", conversation_id=None, subject=None, text="ask finance for Q1 numbers")

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "finance@company.com", "Q1 numbers please")]


def test_replacing_pending_verification_commits_the_delete_even_when_new_target_unresolvable(db_session, session_factory):
    """Critical fix regression test. The old code did `db.delete(pending);
    db.flush()` when a fresh-target message replaces a held query, then (on
    this specific path - the new target doesn't resolve to any registered
    department) returned without ever calling db.commit() again. In
    production, app/ingest/handler.py's `_relay_and_record` closes its
    session in a `finally: db.close()` with no commit, which rolls back an
    uncommitted flush - so the "replace the old pending query" behavior
    would silently not happen. This proves the delete survives an explicit
    session close (mirroring production) and is visible from a totally
    separate, later session."""
    department = _make_department(db_session, team_name="finance")
    _persist_message(db_session, caspian_message_id="msg-first-crit", sender_handle="U-11")
    db_session.add(PendingVerification(
        sender_handle="U-11", channel="slack",
        target_department_id=department.id, message_text="old question",
    ))
    db_session.commit()
    new_message = _persist_message(db_session, caspian_message_id="msg-second-crit", sender_handle="U-11")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="no-such-team", message_text="new question", claims_employee=False, employment_id=None))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=new_message.id, channel="slack", sender_handle="U-11", conversation_id=None, subject=None, text="actually ask no-such-team a new question")

    assert client.initiate_calls == []
    assert len(client.reply_calls) == 1

    db_session.close()

    other_session = session_factory()
    try:
        assert other_session.query(PendingVerification).count() == 0
    finally:
        other_session.close()


def test_followup_message_without_claim_but_with_valid_id_still_verifies(db_session):
    """Important #2 fix: the follow-up answer path must not require
    `claims_employee` - supplying an ID in direct answer to "can you share
    your employee ID?" IS the claim, and requiring the LLM to additionally
    infer claims_employee=True from a bare ID string is unreliable."""
    department = _make_department(db_session, team_name="finance")
    db_session.add(Employee(employment_id="EMP-3", name="Carol"))
    _persist_message(db_session, caspian_message_id="msg-original-3", sender_handle="U-9")
    db_session.add(PendingVerification(
        sender_handle="U-9", channel="slack",
        target_department_id=department.id, message_text="Q1 numbers please",
    ))
    db_session.commit()
    followup = _persist_message(db_session, caspian_message_id="msg-followup-3", sender_handle="U-9")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, employment_id="EMP-3", claims_employee=False))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=followup.id, channel="slack", sender_handle="U-9", conversation_id=None, subject=None, text="EMP-3")

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "finance@company.com", "Q1 numbers please")]
    assert db_session.query(PendingVerification).count() == 0


def test_followup_message_without_any_id_keeps_pending_and_reasks(db_session):
    """Important #3 fix: a follow-up that doesn't attempt an answer at all
    (no employment_id extracted) must not drop the held query - only a
    follow-up that actually supplies an ID (valid or not) should resolve or
    drop the pending row."""
    department = _make_department(db_session, team_name="finance")
    _persist_message(db_session, caspian_message_id="msg-original-4", sender_handle="U-10")
    db_session.add(PendingVerification(
        sender_handle="U-10", channel="slack",
        target_department_id=department.id, message_text="Q1 numbers please",
    ))
    db_session.commit()
    followup = _persist_message(db_session, caspian_message_id="msg-followup-4", sender_handle="U-10")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=False, employment_id=None, claims_employee=False))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=followup.id, channel="slack", sender_handle="U-10", conversation_id=None, subject=None, text="hold on, let me find my badge")

    assert client.initiate_calls == []
    assert len(client.reply_calls) == 1
    pending = db_session.query(PendingVerification).one()
    assert pending.message_text == "Q1 numbers please"


def test_dispatch_failure_replies_with_error_and_records_no_relay_request(db_session):
    _make_department(db_session, team_name="finance")
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="finance", message_text="Q1 numbers please", claims_employee=False, employment_id=None))

    class _FailingClient(_FakeClient):
        def initiate(self, connection_id, recipient, text):
            raise RuntimeError("gateway unreachable")

    client = _FailingClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-1", conversation_id=None, subject=None, text="ask finance for Q1 numbers")

    assert len(client.reply_calls) == 1
    assert client.reply_calls[0][0] == "msg-1"
    assert db_session.query(RelayRequest).count() == 0


def test_lead_email_missing_at_sign_fails_dispatch_without_calling_client(db_session):
    _make_department(db_session, team_name="customercare", requires_verification=False)
    department = db_session.query(Department).filter_by(team_name="customercare").one()
    department.lead_email = "not-an-email"
    db_session.commit()
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="customercare", message_text="need help"))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-1", conversation_id=None, subject=None, text="need help")

    assert client.initiate_calls == []
    assert len(client.reply_calls) == 1


def test_correlation_miss_on_relay_sender_connection_is_not_treated_as_new_request(db_session):
    """A message arriving on the shared relay-sender connection with a
    conversation_id that matches no pending RelayRequest is stray traffic on
    our own outbound channel (e.g. Caspian didn't assign the conversation_id
    we expected), not a genuine new personal-chat request. Treating it as
    one would risk relaying a lead's quoted-reply text onward and asking
    them for an employee ID."""
    message = _persist_message(db_session, sender_handle="lead-address")

    class _RaisingLLM:
        def invoke(self, prompt):
            raise AssertionError("LLM must not be invoked when correlation misses on the relay-sender connection")

    client = _FakeClient()

    run_personal_relay(
        _RaisingLLM(), client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID,
        message_id=message.id, channel="slack", sender_handle="lead-address",
        conversation_id="conv-no-match", subject=None, text="some stray reply text",
        arrived_on_relay_sender_connection=True,
    )

    assert client.initiate_calls == []
    assert client.reply_calls == []
    assert db_session.query(PendingVerification).count() == 0


def test_correlation_miss_on_a_different_connection_still_falls_through_to_normal_handling(db_session):
    """The stop-on-miss guard only applies when the message itself arrived
    on the relay-sender connection - a genuine personal-DM on some other
    platform connection that happens to carry a non-matching conversation_id
    must still be processed normally."""
    _make_department(db_session, team_name="customercare", requires_verification=False)
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="customercare", message_text="need help"))
    client = _FakeClient()

    run_personal_relay(
        llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID,
        message_id=message.id, channel="slack", sender_handle="U-1",
        conversation_id="conv-not-a-relay-reply", subject=None, text="need help",
        arrived_on_relay_sender_connection=False,
    )

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "customercare@company.com", "need help")]


def test_build_personal_prompt_includes_pending_context_when_a_query_is_held(db_session):
    """Regression test: without pending context, a bare ID-only reply like
    "EMP-2" is analyzed by the same generic prompt used for a fresh request,
    risking a hallucinated target_identity that discards the held query
    (see _resolve_pending_with_result's names_fresh_target check)."""
    department = _make_department(db_session, team_name="finance")
    pending = PendingVerification(
        sender_handle="U-1", channel="slack",
        target_department_id=department.id, message_text="Q1 numbers please",
    )
    db_session.add(pending)
    db_session.commit()

    prompt = _build_personal_prompt(subject=None, text="EMP-2", pending=pending)

    assert "finance" in prompt
    assert "Q1 numbers please" in prompt
    assert "employee ID" in prompt

    prompt_without_pending = _build_personal_prompt(subject=None, text="EMP-2", pending=None)
    assert "Q1 numbers please" not in prompt_without_pending


def test_upsert_pending_verification_updates_existing_row_on_conflict(db_session):
    """Simulates the losing side of a concurrent-insert race: a row for
    (sender_handle, channel) already exists when _upsert_pending_verification
    is called, so the plain insert it attempts first must hit the unique
    constraint and fall back to an update rather than propagating the
    IntegrityError (which the caller's top-level except would otherwise
    swallow silently, leaving that sender's message unanswered)."""
    department = _make_department(db_session, team_name="finance")
    other_department = _make_department(db_session, team_name="hr")
    db_session.add(PendingVerification(
        sender_handle="U-1", channel="slack",
        target_department_id=department.id, message_text="old question",
    ))
    db_session.commit()

    _upsert_pending_verification(
        db_session, sender_handle="U-1", channel="slack",
        target_department_id=other_department.id, message_text="new question",
    )

    pending = db_session.query(PendingVerification).one()
    assert pending.message_text == "new question"
    assert pending.target_department_id == other_department.id


def test_duplicate_target_conversation_id_does_not_falsely_report_failure(db_session):
    """Critical fix regression test: if Caspian assigns the send an already-
    in-use conversation_id (e.g. two relays to the same lead collapse onto
    one mailbox thread), the email has ALREADY gone out by the time the
    RelayRequest insert conflicts. The requester must not be told the relay
    failed - it didn't - and the original pending RelayRequest must survive
    untouched rather than being silently overwritten or lost."""
    _make_department(db_session, team_name="finance", requires_verification=False)
    earlier_message = _persist_message(db_session, caspian_message_id="msg-earlier", sender_handle="U-earlier")
    db_session.add(RelayRequest(
        source_message_id=earlier_message.id, source_identity="personal",
        target_identity="finance", target_conversation_id="conv-dup",
        message_text="earlier question", status="pending",
    ))
    db_session.commit()

    message = _persist_message(db_session, caspian_message_id="msg-new", sender_handle="U-new")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="finance", message_text="new question", claims_employee=False, employment_id=None))
    client = _FakeClient(initiate_response={"conversation_id": "conv-dup"})

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-new", conversation_id=None, subject=None, text="ask finance a new question")

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "finance@company.com", "new question")]
    assert client.reply_calls == []  # no false "couldn't relay" message - it was sent

    relay_requests = db_session.query(RelayRequest).all()
    assert len(relay_requests) == 1
    assert relay_requests[0].message_text == "earlier question"  # untouched


def test_reply_correlation_delivers_reply_to_original_message_and_completes_pending(db_session):
    """The lead's reply to a dispatched relay arrives back on the same
    shared relay-sender connection as a new inbound message, which
    classify_scope will itself classify as personal scope and route back
    into run_personal_relay. That inbound message's `conversation_id` must
    be matched against a pending RelayRequest FIRST, and the reply
    delivered to the ORIGINAL requester's message, not the lead's."""

    class _RaisingLLM:
        def invoke(self, prompt):
            raise AssertionError("LLM must not be invoked on the reply-correlation path")

    original = _persist_message(db_session, caspian_message_id="msg-original-reply", sender_handle="U-8")
    relay_request = RelayRequest(
        source_message_id=original.id,
        source_identity="personal",
        target_identity="finance",
        target_conversation_id="conv-lead-reply-1",
        message_text="Q1 numbers please",
        status="pending",
    )
    db_session.add(relay_request)
    db_session.commit()

    followup = _persist_message(db_session, caspian_message_id="msg-followup-reply", sender_handle="lead-address")
    client = _FakeClient()

    run_personal_relay(_RaisingLLM(), client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=followup.id, channel="slack", sender_handle="lead-address", conversation_id="conv-lead-reply-1", subject=None, text="Here are the Q1 numbers: $5M")

    assert client.reply_calls == [("msg-original-reply", "Here are the Q1 numbers: $5M")]
    db_session.refresh(relay_request)
    assert relay_request.status == "completed"
    assert relay_request.completed_at is not None
