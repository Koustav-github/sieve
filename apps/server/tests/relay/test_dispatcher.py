import pytest

from app.relay.dispatcher import deliver_reply, resolve_identity_address, send_relay


class _FakeClient:
    def __init__(self, initiate_response=None, reply_response=None):
        self.initiate_calls = []
        self.reply_calls = []
        self._initiate_response = initiate_response or {"conversation_id": "conv-1"}
        self._reply_response = reply_response or {"id": "reply-1"}

    def initiate(self, connection_id, recipient, text):
        self.initiate_calls.append((connection_id, recipient, text))
        return self._initiate_response

    def reply(self, message_id, text=None, **kwargs):
        self.reply_calls.append((message_id, text))
        return self._reply_response


def test_resolve_identity_address_prefers_address_key():
    assert resolve_identity_address({"id": "conn-1", "address": "internal@sieve.test"}) == "internal@sieve.test"


def test_resolve_identity_address_falls_back_to_email_key():
    assert resolve_identity_address({"id": "conn-1", "email": "internal@sieve.test"}) == "internal@sieve.test"


def test_resolve_identity_address_falls_back_to_username_key():
    assert resolve_identity_address({"id": "conn-1", "username": "internal"}) == "internal"


def test_resolve_identity_address_raises_when_no_known_key():
    with pytest.raises(KeyError):
        resolve_identity_address({"id": "conn-1"})


def test_send_relay_calls_initiate_and_returns_conversation_id():
    client = _FakeClient(initiate_response={"conversation_id": "conv-99"})

    conversation_id = send_relay(client, connection_id="conn-support", recipient="internal@sieve.test", text="hello")

    assert conversation_id == "conv-99"
    assert client.initiate_calls == [("conn-support", "internal@sieve.test", "hello")]


def test_send_relay_falls_back_to_id_key_in_response():
    client = _FakeClient(initiate_response={"id": "conv-fallback"})

    conversation_id = send_relay(client, connection_id="conn-support", recipient="internal@sieve.test", text="hello")

    assert conversation_id == "conv-fallback"


def test_send_relay_raises_when_response_has_no_known_key():
    client = _FakeClient(initiate_response={"status": "ok"})

    with pytest.raises(KeyError):
        send_relay(client, connection_id="conn-support", recipient="internal@sieve.test", text="hello")


def test_deliver_reply_calls_reply_with_message_id_and_text():
    client = _FakeClient()

    deliver_reply(client, caspian_message_id="msg-abc", text="here's the answer")

    assert client.reply_calls == [("msg-abc", "here's the answer")]
