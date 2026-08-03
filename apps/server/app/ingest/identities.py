from caspian_sdk import CommClient

from app.core.config import settings

IDENTITY_CHANNELS: dict[str, list[str]] = {
    "careers": ["email"],
    "support": ["email", "telegram"],
    "internal": ["email", "slack", "discord"],
}


def register_identities(client: CommClient) -> None:
    """Register Sieve's 3 fixed agent identities (careers/support/internal) on one CommClient.

    NOTE: the real installed caspian-sdk API (introspected at
    apps/server/app/ingest, see task-6-report.md) does NOT expose a
    consistent `username=` kwarg across all four connect methods the way
    external docs implied:
      - connect_email:    has `username=` (readable mailbox name).
      - connect_telegram: NO `username=` param; the bot token itself is
                           the identity.
      - install_slack:    NO `username=` param; `display_name=` is the
                           human-facing name for a shared-app install.
      - connect_discord:  HAS `username=` (webhook-based per-agent name).

    The one parameter present on all four methods is `agent_id=`, which
    is the platform's actual mechanism for associating a channel
    connection with one of Sieve's fixed identities. We pass
    `agent_id=identity` on every call, and additionally pass
    `username=`/`display_name=` where the channel supports a human-facing
    name.
    """
    for identity, channels in IDENTITY_CHANNELS.items():
        if "email" in channels:
            client.connect_email(username=identity, agent_id=identity)
        if "telegram" in channels:
            client.connect_telegram(
                bot_token=settings.telegram_bot_token, agent_id=identity
            )
        if "slack" in channels:
            client.install_slack(
                agent_id=identity, display_name=f"Sieve ({identity})"
            )
        if "discord" in channels:
            client.connect_discord(
                bot_token=settings.discord_bot_token,
                username=identity,
                agent_id=identity,
            )
