from app.ingest.message_store import persist_message
from app.models.department import Department
from app.models.platform_connection import PlatformConnection
from app.relay.group_pipeline import run_group_relay
from app.relay.schemas import RelayExtractionResult


class _FakeLLM:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        return self.result


class _FakeClient:
    def __init__(self):
        self.send_calls = []
        self.reply_calls = []

    def send_message(self, conversation_id, text=None, **kwargs):
        self.send_calls.append((conversation_id, text))
        return {"id": "sent-1"}

    def reply(self, message_id, text=None, **kwargs):
        self.reply_calls.append((message_id, text))
        return {"id": "reply-1"}


def _make_department(db_session, *, team_name, channel_ref, requires_verification=True, connection_id="conn-slack-1"):
    platform_connection = (
        db_session.query(PlatformConnection).filter_by(connection_id=connection_id).one_or_none()
    )
    if platform_connection is None:
        platform_connection = PlatformConnection(platform="slack", connection_id=connection_id)
        db_session.add(platform_connection)
        db_session.flush()
    department = Department(
        team_name=team_name, lead_name="Lead", lead_email=f"{team_name}@company.com",
        platform_connection_id=platform_connection.id, channel_ref=channel_ref,
        requires_verification=requires_verification,
    )
    db_session.add(department)
    db_session.commit()
    return department


def _persist_message(db_session, caspian_message_id="msg-1"):
    message = persist_message(
        db_session, caspian_message_id=caspian_message_id, agent_id="finance",
        channel="slack", sender_handle="U-1", thread_id="chan-finance", raw_payload={},
    )
    db_session.commit()
    return message


def test_non_relay_message_does_nothing(db_session):
    source = _make_department(db_session, team_name="finance", channel_ref="chan-finance")
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=False))
    client = _FakeClient()

    run_group_relay(llm, client, db_session, message_id=message.id, source_department=source, subject=None, text="just chatting")

    assert client.send_calls == []


def test_matched_target_delivers_into_target_group_chat(db_session):
    source = _make_department(db_session, team_name="finance", channel_ref="chan-finance")
    _make_department(db_session, team_name="management", channel_ref="chan-management")
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="management", message_text="need Q1 numbers"))
    client = _FakeClient()

    run_group_relay(llm, client, db_session, message_id=message.id, source_department=source, subject=None, text="ask management for Q1 numbers")

    assert client.send_calls == [("chan-management", "need Q1 numbers")]


def test_unmatched_target_falls_back_to_exempt_department(db_session):
    source = _make_department(db_session, team_name="finance", channel_ref="chan-finance")
    _make_department(db_session, team_name="customercare", channel_ref="chan-cc", requires_verification=False)
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="legal", message_text="need a contract review"))
    client = _FakeClient()

    run_group_relay(llm, client, db_session, message_id=message.id, source_department=source, subject=None, text="ask legal about the contract")

    assert client.send_calls == [("chan-cc", "need a contract review")]


def test_self_relay_is_skipped(db_session):
    source = _make_department(db_session, team_name="finance", channel_ref="chan-finance")
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="finance", message_text="ask finance about finance"))
    client = _FakeClient()

    run_group_relay(llm, client, db_session, message_id=message.id, source_department=source, subject=None, text="ask finance something")

    assert client.send_calls == []


def test_delivery_failure_replies_into_source_group_chat(db_session):
    source = _make_department(db_session, team_name="finance", channel_ref="chan-finance")
    _make_department(db_session, team_name="management", channel_ref="chan-management")
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="management", message_text="need Q1 numbers"))

    class _FailingClient(_FakeClient):
        def send_message(self, conversation_id, text=None, **kwargs):
            raise RuntimeError("gateway unreachable")

    client = _FailingClient()

    run_group_relay(llm, client, db_session, message_id=message.id, source_department=source, subject=None, text="ask management")

    assert len(client.reply_calls) == 1
    assert client.reply_calls[0][0] == "msg-1"
