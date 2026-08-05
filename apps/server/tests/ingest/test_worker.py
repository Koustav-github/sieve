import app.ingest.worker as worker_module


class _FakeClient:
    def __init__(self):
        self.on_message_calls = []
        self.listen_called = False

    def connect_email(self, username):
        return {"id": f"conn-{username}"}

    def on_message(self, handler):
        self.on_message_calls.append(handler)
        return handler

    def listen(self):
        self.listen_called = True


def test_main_connects_relay_sender_and_reaches_listen(monkeypatch, session_factory):
    fake_client = _FakeClient()
    monkeypatch.setattr(worker_module, "CommClient", lambda: fake_client)
    monkeypatch.setattr(worker_module, "SessionLocal", session_factory)
    monkeypatch.setattr(worker_module, "build_relay_llm", lambda: object())

    worker_module.main()

    assert fake_client.listen_called is True
    assert len(fake_client.on_message_calls) == 1


def test_main_passes_relay_sender_connection_id_to_handler_builder(monkeypatch, session_factory):
    fake_client = _FakeClient()
    monkeypatch.setattr(worker_module, "CommClient", lambda: fake_client)
    monkeypatch.setattr(worker_module, "SessionLocal", session_factory)
    monkeypatch.setattr(worker_module, "build_relay_llm", lambda: object())
    captured = {}

    def fake_build_on_message_handler(session_factory_arg, relay_llm, executor, client, relay_sender_connection_id):
        captured["relay_sender_connection_id"] = relay_sender_connection_id
        return lambda message: None

    monkeypatch.setattr(worker_module, "build_on_message_handler", fake_build_on_message_handler)

    worker_module.main()

    assert captured["relay_sender_connection_id"] == "conn-relay"
