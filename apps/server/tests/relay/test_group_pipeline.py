import logging

from app.ingest.message_store import persist_message
from app.models.department import Department
from app.models.platform_connection import PlatformConnection
from app.relay.group_pipeline import NO_MATCH_REPLY_TEXT, run_group_relay
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


def test_exempt_department_misconfiguration_is_logged_not_replied(db_session, caplog):
    """get_exempt_department raises RuntimeError when more than one department
    has requires_verification=False - a data-integrity misconfiguration, not a
    routine failure. run_group_relay has no dedicated try/except around that
    call, so it's only caught by the outer top-level safety net, which logs
    and swallows but must NOT send a user-facing reply (that would incorrectly
    imply the request was understood and just didn't match anything - see
    test_unmatched_target_falls_back_to_exempt_department and
    test_genuine_no_match_sends_no_match_reply for the contrast)."""
    source = _make_department(db_session, team_name="finance", channel_ref="chan-finance")
    _make_department(db_session, team_name="customercare", channel_ref="chan-cc", requires_verification=False)
    _make_department(db_session, team_name="support", channel_ref="chan-support", requires_verification=False)
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="legal", message_text="need a contract review"))
    client = _FakeClient()

    with caplog.at_level(logging.ERROR, logger="app.relay.group_pipeline"):
        run_group_relay(llm, client, db_session, message_id=message.id, source_department=source, subject=None, text="ask legal about the contract")

    assert client.send_calls == []
    assert client.reply_calls == []
    pipeline_records = [r for r in caplog.records if r.name == "app.relay.group_pipeline"]
    assert any(r.levelno >= logging.ERROR for r in pipeline_records)


def test_genuine_no_match_sends_no_match_reply(db_session):
    """When the target doesn't resolve AND no exempt department is
    configured at all (get_exempt_department returns None, not a
    RuntimeError - zero exempt departments, not more than one), the sender
    must be told via NO_MATCH_REPLY_TEXT. This is the genuine-no-match
    branch, distinct from the misconfiguration branch above which replies to
    no one."""
    source = _make_department(db_session, team_name="finance", channel_ref="chan-finance")
    _make_department(db_session, team_name="management", channel_ref="chan-management", requires_verification=True)
    message = _persist_message(db_session)
    llm = _FakeLLM(RelayExtractionResult(is_relay_request=True, target_identity="legal", message_text="need a contract review"))
    client = _FakeClient()

    run_group_relay(llm, client, db_session, message_id=message.id, source_department=source, subject=None, text="ask legal about the contract")

    assert client.send_calls == []
    assert client.reply_calls == [("msg-1", NO_MATCH_REPLY_TEXT)]
