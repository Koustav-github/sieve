import logging
from collections.abc import Callable
from functools import partial

from caspian_sdk import CommClient, CommError

from app.core.config import settings

logger = logging.getLogger(__name__)

IDENTITY_CHANNELS: dict[str, list[str]] = {
    "careers": ["email"],
    "support": ["email", "telegram"],
    "internal": ["email", "slack", "discord"],
}

# Sentinel stored in the results dict for a channel that came back 409 ("name
# taken"): the gateway has confirmed this identity/channel is already
# registered. That is the expected steady state from the second container
# restart onward, not a failure - see `_register_channel` below.
ALREADY_REGISTERED = "already_registered"


def register_identities(client: CommClient) -> dict[tuple[str, str], dict | str | None]:
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

    Idempotency / fault tolerance (final-review C1 fix): this function is
    called on every container start, including every crash-loop restart
    under `restart: unless-stopped`. Registration is therefore made
    non-fatal per channel:
      - A 409 ("name taken") from the gateway means this identity/channel is
        already registered from a previous run - expected from restart #2
        onward, logged at info level, not raised.
      - Any other `CommError` (e.g. a genuinely bad request) is logged as a
        warning and that one channel is skipped; every other channel/identity
        still gets attempted.
      - A channel whose required secret (bot token) is blank is skipped
        outright rather than sent to the gateway as `""`, which would just
        produce another 4xx.
    The caller (worker.main) decides what "every channel failed" means for
    the process as a whole; this function never raises for a per-channel
    failure so it always finishes and lets the worker reach `client.listen()`.

    ASSUMPTION requiring live verification (I3, deferred to Task 9): we
    assume the `agent_id` string passed on every connect_*/install_* call
    below is exactly what the gateway echoes back on inbound events, i.e.
    that `caspian_sdk.client.Message.agent_id` on a real inbound message
    equals the literal `identity` string ("careers"/"support"/"internal") we
    registered it under here. `app/ingest/handler.py` relies on this for its
    coarse-bucket semantic (`messages.agent_id` must be exactly one of those
    three values). This cannot be confirmed without a live gateway and real
    credentials, which this task does not have - do not assume it is true in
    production until Task 9 verifies it end-to-end against the real Caspian
    gateway.

    Returns a dict of ``{(identity, channel): result}`` where ``result`` is:
      - the connection dict returned by the gateway on success,
      - ``ALREADY_REGISTERED`` on a 409 (identity/channel confirmed already
        registered, not a failure),
      - ``None`` if the channel was skipped (blank secret) or genuinely
        failed to register.
    So the worker can log whatever identity/address info Caspian actually
    assigned (for Task 9 to inspect), and can tell whether every channel
    failed outright.
    """
    results: dict[tuple[str, str], dict | str | None] = {}

    for identity, channels in IDENTITY_CHANNELS.items():
        if "email" in channels:
            results[(identity, "email")] = _register_channel(
                identity, "email",
                partial(client.connect_email, username=identity, agent_id=identity),
            )
        if "telegram" in channels:
            if not settings.telegram_bot_token:
                logger.warning(
                    "Skipping telegram registration for %s: TELEGRAM_BOT_TOKEN is blank",
                    identity,
                )
                results[(identity, "telegram")] = None
            else:
                results[(identity, "telegram")] = _register_channel(
                    identity, "telegram",
                    partial(
                        client.connect_telegram,
                        bot_token=settings.telegram_bot_token, agent_id=identity,
                    ),
                )
        if "slack" in channels:
            results[(identity, "slack")] = _register_channel(
                identity, "slack",
                partial(
                    client.install_slack,
                    agent_id=identity, display_name=f"Sieve ({identity})",
                ),
            )
        if "discord" in channels:
            if not settings.discord_bot_token:
                logger.warning(
                    "Skipping discord registration for %s: DISCORD_BOT_TOKEN is blank",
                    identity,
                )
                results[(identity, "discord")] = None
            else:
                results[(identity, "discord")] = _register_channel(
                    identity, "discord",
                    partial(
                        client.connect_discord,
                        bot_token=settings.discord_bot_token,
                        username=identity,
                        agent_id=identity,
                    ),
                )

    return results


def _register_channel(identity: str, channel: str, connect: Callable[[], dict]) -> dict | str | None:
    """Run one `connect_*`/`install_*` call, translating a `CommError` into a
    non-fatal per-channel result instead of letting it propagate. See
    `register_identities` docstring for the 409-is-expected reasoning."""
    try:
        connection = connect()
    except CommError as exc:
        if exc.status_code == 409:
            logger.info(
                "%s/%s already registered (409, name taken) - expected on "
                "restart, not fatal",
                identity,
                channel,
            )
            return ALREADY_REGISTERED
        logger.warning(
            "Failed to register %s/%s: %s", identity, channel, exc
        )
        return None
    logger.info("Registered %s/%s: %s", identity, channel, connection)
    return connection
