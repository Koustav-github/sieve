import pytest

import app.ingest.worker as worker_module


class _FakeClient:
    def __init__(self):
        self.on_message_calls = []
        self.listen_called = False

    def on_message(self, handler):
        self.on_message_calls.append(handler)
        return handler

    def listen(self):
        self.listen_called = True


def test_main_reaches_listen_with_partial_registration_failures(monkeypatch):
    """C1: main() must always reach client.listen() when at least one channel
    registered successfully, regardless of other channels' outcomes."""
    fake_client = _FakeClient()
    monkeypatch.setattr(worker_module, "CommClient", lambda: fake_client)
    monkeypatch.setattr(
        worker_module,
        "register_identities",
        lambda client: {
            ("careers", "email"): {"id": "conn-1"},
            ("support", "email"): None,
        },
    )

    worker_module.main()

    assert fake_client.listen_called is True
    assert len(fake_client.on_message_calls) == 1


def test_main_raises_when_every_channel_fails(monkeypatch):
    """C1: the only intentional fatal case is when literally every channel
    failed to register - main() must not reach listen() in that case."""
    fake_client = _FakeClient()
    monkeypatch.setattr(worker_module, "CommClient", lambda: fake_client)
    monkeypatch.setattr(
        worker_module,
        "register_identities",
        lambda client: {
            ("careers", "email"): None,
            ("support", "email"): None,
        },
    )

    with pytest.raises(RuntimeError):
        worker_module.main()

    assert fake_client.listen_called is False


def test_main_reaches_listen_when_results_empty(monkeypatch):
    """Defensive: an empty results dict must not be treated as 'every channel
    failed' (there's nothing to have failed)."""
    fake_client = _FakeClient()
    monkeypatch.setattr(worker_module, "CommClient", lambda: fake_client)
    monkeypatch.setattr(worker_module, "register_identities", lambda client: {})

    worker_module.main()

    assert fake_client.listen_called is True


def test_main_reaches_listen_when_all_already_registered(monkeypatch):
    """C1: a restart where every channel comes back 409 (ALREADY_REGISTERED)
    must not be treated as 'every channel failed' - it's the expected
    steady state from restart #2 onward."""
    from app.ingest.identities import ALREADY_REGISTERED

    fake_client = _FakeClient()
    monkeypatch.setattr(worker_module, "CommClient", lambda: fake_client)
    monkeypatch.setattr(
        worker_module,
        "register_identities",
        lambda client: {
            ("careers", "email"): ALREADY_REGISTERED,
            ("support", "email"): ALREADY_REGISTERED,
        },
    )

    worker_module.main()

    assert fake_client.listen_called is True
