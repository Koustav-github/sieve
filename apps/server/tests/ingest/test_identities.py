from caspian_sdk import CommError

from app.core.config import settings
from app.ingest.identities import (
    ALREADY_REGISTERED,
    connection_identity_map,
    register_identities,
)


class _FakeClient:
    """Stand-in for caspian_sdk.CommClient that never talks to a real gateway.

    `behaviors` maps a channel name ("email"/"telegram"/"slack"/"discord") to
    either a connection dict to return, or an exception instance to raise.
    Defaults to returning a trivial connection dict for any channel not
    given an explicit behavior.
    """

    def __init__(self, behaviors: dict | None = None):
        self.behaviors = behaviors or {}
        self.calls: list[tuple[str, dict]] = []

    def _resolve(self, channel: str, **kwargs) -> dict:
        self.calls.append((channel, kwargs))
        behavior = self.behaviors.get(channel)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior or {"id": f"conn-{channel}", "status": "active"}

    def connect_email(self, **kwargs):
        return self._resolve("email", **kwargs)

    def connect_telegram(self, **kwargs):
        return self._resolve("telegram", **kwargs)

    def install_slack(self, **kwargs):
        return self._resolve("slack", **kwargs)

    def connect_discord(self, **kwargs):
        return self._resolve("discord", **kwargs)


def test_register_identities_returns_connections_on_success(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "tg-token")
    monkeypatch.setattr(settings, "discord_bot_token", "dc-token")

    client = _FakeClient()
    results = register_identities(client)

    # careers=email, support=email+telegram, internal=email+slack+discord
    assert results[("careers", "email")] == {"id": "conn-email", "status": "active"}
    assert results[("support", "telegram")] == {"id": "conn-telegram", "status": "active"}
    assert results[("internal", "slack")] == {"id": "conn-slack", "status": "active"}
    assert results[("internal", "discord")] == {"id": "conn-discord", "status": "active"}


def test_register_identities_never_passes_agent_id_or_customer_id(monkeypatch):
    """Live sandbox correction: agent_id requires a paired customer_id from
    Caspian's multi-tenant create_customer()/create_agent() flow (422
    otherwise) - Sieve doesn't do multi-tenant isolation, so neither is
    passed on any of the 4 connect_*/install_* calls."""
    monkeypatch.setattr(settings, "telegram_bot_token", "tg-token")
    monkeypatch.setattr(settings, "discord_bot_token", "dc-token")

    client = _FakeClient()
    register_identities(client)

    for _channel, kwargs in client.calls:
        assert "agent_id" not in kwargs
        assert "customer_id" not in kwargs


def test_connection_identity_map_extracts_ids_from_successful_results():
    results = {
        ("careers", "email"): {"id": "conn-careers-email", "status": "active"},
        ("support", "email"): {"id": "conn-support-email", "status": "active"},
        ("support", "telegram"): {"id": "conn-support-telegram", "status": "active"},
    }

    mapping = connection_identity_map(results)

    assert mapping == {
        "conn-careers-email": "careers",
        "conn-support-email": "support",
        "conn-support-telegram": "support",
    }


def test_connection_identity_map_skips_non_dict_results():
    """A 409 (ALREADY_REGISTERED) or None (skipped/failed) result contributes
    no connection_id - there's nothing to map."""
    results = {
        ("careers", "email"): ALREADY_REGISTERED,
        ("support", "telegram"): None,
        ("internal", "slack"): {"id": "conn-internal-slack", "status": "pending_oauth"},
    }

    mapping = connection_identity_map(results)

    assert mapping == {"conn-internal-slack": "internal"}


def test_register_identities_treats_409_as_non_fatal_and_continues(monkeypatch):
    """C1: a 409 ('name taken') on one channel must not stop registration of
    the rest, and must be reported distinctly (not as a plain failure)."""
    monkeypatch.setattr(settings, "telegram_bot_token", "tg-token")
    monkeypatch.setattr(settings, "discord_bot_token", "dc-token")

    client = _FakeClient(behaviors={"email": CommError(409, "name taken")})
    results = register_identities(client)

    assert results[("careers", "email")] == ALREADY_REGISTERED
    # Every other channel still got attempted despite the email 409s.
    assert results[("support", "telegram")] is not None
    assert results[("internal", "slack")] is not None
    assert results[("internal", "discord")] is not None


def test_register_identities_logs_and_continues_on_other_errors(monkeypatch, caplog):
    """C1: a non-409 CommError on one channel is logged and skipped, not raised -
    registration must reach every other channel/identity regardless."""
    monkeypatch.setattr(settings, "telegram_bot_token", "tg-token")
    monkeypatch.setattr(settings, "discord_bot_token", "dc-token")

    client = _FakeClient(behaviors={"slack": CommError(400, "bad request")})

    results = register_identities(client)  # must not raise

    assert results[("internal", "slack")] is None
    # Discord (same identity, registered after slack) still attempted.
    assert results[("internal", "discord")] is not None
    assert results[("careers", "email")] is not None


def test_register_identities_skips_blank_bot_tokens(monkeypatch):
    """C1: a blank bot token must be skipped rather than sent to the gateway
    as an empty string (which would just produce another 4xx)."""
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "discord_bot_token", "")

    client = _FakeClient()
    results = register_identities(client)

    assert results[("support", "telegram")] is None
    assert results[("internal", "discord")] is None
    assert not any(channel == "telegram" for channel, _ in client.calls)
    assert not any(channel == "discord" for channel, _ in client.calls)
    # Email/slack (no secret requirement) still registered normally.
    assert results[("careers", "email")] is not None
    assert results[("internal", "slack")] is not None
