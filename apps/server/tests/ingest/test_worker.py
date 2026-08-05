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


def _patch_common(monkeypatch, session_factory, fake_client):
    monkeypatch.setattr(worker_module, "CommClient", lambda: fake_client)
    monkeypatch.setattr(worker_module, "SessionLocal", session_factory)
    monkeypatch.setattr(worker_module, "build_relay_llm", lambda: object())


def test_main_reaches_listen_with_partial_registration_failures(monkeypatch, session_factory):
    """C1: main() must always reach client.listen() when at least one channel
    registered successfully, regardless of other channels' outcomes - as long
    as every identity's *email* coverage (the required-for-all-3 channel) is
    still fully resolved. Here support's telegram fails but all 3 identities'
    email registrations succeed, so listen() must still be reached."""
    fake_client = _FakeClient()
    _patch_common(monkeypatch, session_factory, fake_client)
    monkeypatch.setattr(
        worker_module,
        "register_identities",
        lambda client: {
            ("careers", "email"): {"id": "conn-careers-email"},
            ("support", "email"): {"id": "conn-support-email"},
            ("support", "telegram"): None,
            ("internal", "email"): {"id": "conn-internal-email"},
        },
    )

    worker_module.main()

    assert fake_client.listen_called is True
    assert len(fake_client.on_message_calls) == 1


def test_main_raises_when_every_channel_fails(monkeypatch, session_factory):
    """C1: the only intentional fatal case is when literally every channel
    failed to register - main() must not reach listen() in that case."""
    fake_client = _FakeClient()
    _patch_common(monkeypatch, session_factory, fake_client)
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


def test_main_raises_when_results_empty(monkeypatch, session_factory):
    """An empty results dict means no identity has resolved email coverage -
    the "every channel failed" shortcut doesn't fire (nothing to call
    "failed"), but validate_identity_coverage() must still refuse to start
    listen() with zero identities able to route inbound email."""
    fake_client = _FakeClient()
    _patch_common(monkeypatch, session_factory, fake_client)
    monkeypatch.setattr(worker_module, "register_identities", lambda client: {})

    with pytest.raises(RuntimeError):
        worker_module.main()

    assert fake_client.listen_called is False


def test_main_raises_when_email_identity_already_registered_with_no_others(monkeypatch, session_factory):
    """A 409 (ALREADY_REGISTERED) on an identity's email gives us no
    connection_id to route on. Per the live-verified idempotency of
    connect_email() (see identities.py), a real restart returns the same
    connection dict again, not a 409 - so this combination means a genuine
    external conflict, and the worker must not proceed with identities that
    can't route inbound mail."""
    from app.ingest.identities import ALREADY_REGISTERED

    fake_client = _FakeClient()
    _patch_common(monkeypatch, session_factory, fake_client)
    monkeypatch.setattr(
        worker_module,
        "register_identities",
        lambda client: {
            ("careers", "email"): ALREADY_REGISTERED,
            ("support", "email"): ALREADY_REGISTERED,
            ("internal", "email"): ALREADY_REGISTERED,
        },
    )

    with pytest.raises(RuntimeError):
        worker_module.main()

    assert fake_client.listen_called is False


def test_main_builds_identity_email_connections_from_email_results_only(monkeypatch, session_factory):
    """The dict threaded into build_on_message_handler for relay dispatch
    must contain only the email connections (relay always goes out over
    email - see Global Constraints), keyed by identity, not by (identity,
    channel), and must exclude non-dict results (None/ALREADY_REGISTERED)
    and non-email channels."""
    fake_client = _FakeClient()
    _patch_common(monkeypatch, session_factory, fake_client)
    monkeypatch.setattr(
        worker_module,
        "register_identities",
        lambda client: {
            ("careers", "email"): {"id": "conn-careers-email"},
            ("support", "email"): {"id": "conn-support-email"},
            ("support", "telegram"): {"id": "conn-support-telegram"},
            ("internal", "email"): {"id": "conn-internal-email"},
            ("internal", "slack"): None,
        },
    )
    captured = {}

    def fake_build_on_message_handler(session_factory_arg, connection_identities, relay_llm, executor, client, identity_email_connections):
        captured["identity_email_connections"] = identity_email_connections
        return lambda message: None

    monkeypatch.setattr(worker_module, "build_on_message_handler", fake_build_on_message_handler)

    worker_module.main()

    assert captured["identity_email_connections"] == {
        "careers": {"id": "conn-careers-email"},
        "support": {"id": "conn-support-email"},
        "internal": {"id": "conn-internal-email"},
    }
