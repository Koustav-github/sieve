from app.ingest.message_store import persist_message
from app.models.department import Department
from app.models.employee import Employee
from app.models.pending_verification import PendingVerification
from app.models.person import PersonEntity
from app.models.platform_connection import PlatformConnection
from app.relay.personal_pipeline import run_personal_relay
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

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-1", subject=None, text="need help")

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "customercare@company.com", "need help")]
    assert db_session.query(PendingVerification).count() == 0


def test_no_id_given_asks_for_one_and_holds_the_query(db_session):
    _make_department(db_session, team_name="finance")
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="finance", message_text="Q1 numbers please", claims_employee=False, employment_id=None))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-1", subject=None, text="ask finance for Q1 numbers")

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

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-2", subject=None, text="my id is EMP-1, ask finance for Q1 numbers")

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "finance@company.com", "Q1 numbers please")]
    person = db_session.query(PersonEntity).filter_by(is_provisional=True).one()
    assert person.verified_employee is True


def test_followup_message_with_valid_id_resumes_held_query(db_session):
    department = _make_department(db_session, team_name="finance")
    db_session.add(Employee(employment_id="EMP-2", name="Bob"))
    original = _persist_message(db_session, caspian_message_id="msg-original", sender_handle="U-3")
    db_session.add(PendingVerification(
        sender_handle="U-3", channel="slack",
        target_department_id=department.id, message_text="Q1 numbers please",
    ))
    db_session.commit()
    followup = _persist_message(db_session, caspian_message_id="msg-followup", sender_handle="U-3")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, employment_id="EMP-2", claims_employee=True))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=followup.id, channel="slack", sender_handle="U-3", subject=None, text="EMP-2")

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "finance@company.com", "Q1 numbers please")]
    assert db_session.query(PendingVerification).count() == 0


def test_followup_message_with_invalid_id_drops_pending_and_replies(db_session):
    department = _make_department(db_session, team_name="finance")
    original = _persist_message(db_session, caspian_message_id="msg-original-2", sender_handle="U-4")
    db_session.add(PendingVerification(
        sender_handle="U-4", channel="slack",
        target_department_id=department.id, message_text="Q1 numbers please",
    ))
    db_session.commit()
    followup = _persist_message(db_session, caspian_message_id="msg-followup-2", sender_handle="U-4")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, employment_id="does-not-exist", claims_employee=True))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=followup.id, channel="slack", sender_handle="U-4", subject=None, text="does-not-exist")

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

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=new_message.id, channel="slack", sender_handle="U-5", subject=None, text="actually ask finance a new question")

    pending = db_session.query(PendingVerification).one()
    assert pending.message_text == "new question"


def test_already_verified_employee_skips_reasking(db_session):
    department = _make_department(db_session, team_name="finance")
    from app.models.channel_handle import ChannelHandle
    person = PersonEntity(display_name=None, is_provisional=True, verified_employee=True)
    db_session.add(person)
    db_session.flush()
    db_session.add(ChannelHandle(person_entity_id=person.id, channel="slack", handle="U-6"))
    db_session.commit()
    message = _persist_message(db_session, sender_handle="U-6")
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="finance", message_text="Q1 numbers please", claims_employee=False, employment_id=None))
    client = _FakeClient()

    run_personal_relay(llm, client, db_session, connection_id=RELAY_SENDER_CONNECTION_ID, message_id=message.id, channel="slack", sender_handle="U-6", subject=None, text="ask finance for Q1 numbers")

    assert client.initiate_calls == [(RELAY_SENDER_CONNECTION_ID, "finance@company.com", "Q1 numbers please")]
