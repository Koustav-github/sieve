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

# KNOWN LIMITATION, live-verified against the real sandbox gateway (Task 9):
# without customer_id/agent_id, `connect_email(username=...)` does NOT give
# each identity its own mailbox on this project - the sandbox returned the
# SAME existing connection/address for every username tried once one email
# connection existed, so careers/support/internal's email presence collapses
# onto one shared inbox. The properly-namespaced fix is Caspian's own
# multi-tenant flow (one create_customer() + one create_agent() per identity,
# then connect_email(customer_id=..., agent_id=...) - confirmed live to give
# each identity a genuinely distinct address, e.g. careers@... vs
# internal@...). That flow was NOT wired in here because create_customer()/
# create_agent() are themselves not idempotent (confirmed live: repeat calls
# mint new resources every time, not the same one back) - using them safely
# needs the created customer_id/agent_id/connection_id persisted somewhere
# that survives a worker restart, which is a real, separate piece of work
# (e.g. a small table, or writing them to .env) deferred to a follow-up
# rather than solved ad hoc here. Slack/Discord/Telegram are NOT affected -
# they're already scoped per identity via separate bot tokens/webhooks, not
# a shared name-uniqueness pool like email.

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

    LIVE-VERIFIED CORRECTION (Task 9 smoke test against the real sandbox
    gateway): `agent_id=` is NOT a free-form label. The gateway rejected
    every `agent_id=identity` call with `422: Provide both customer_id and
    agent_id, or neither` - `agent_id` is only valid when paired with a
    `customer_id` from Caspian's multi-tenant `create_customer()`/
    `create_agent()` resource flow (see the SDK's own multi-tenant example).
    Sieve is a single organisation running 3 fixed identities, not a
    multi-tenant platform serving multiple customers, so that flow doesn't
    apply here - we pass NEITHER `customer_id` nor `agent_id`. Identity is
    instead distinguished purely by `username=`/bot token/`display_name=`
    per channel (the "same agent across channels with unique identity"
    pattern from the SDK's docs), which is exactly what Sieve needs.

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

    COARSE-BUCKET NOTE (I3, resolved by the correction above): since we no
    longer send `agent_id` at all, `app/ingest/handler.py` cannot rely on
    `caspian_sdk.client.Message.agent_id` to carry Sieve's identity label -
    the live sandbox confirmed the gateway assigns its OWN platform-internal
    agent_id (e.g. "agt_a6cdd09cd6d882a26aaa72a2") even when we don't set
    one, unrelated to our "careers"/"support"/"internal" labels. Instead,
    `handler.py` resolves the coarse bucket from `Message.connection_id` via
    `connection_identity_map()` below, built from THIS function's own
    return value.

    Idempotency was also verified live: calling e.g. `connect_email(
    username="careers")` twice (without `agent_id`/`customer_id`) returns
    the *same* connection `id` both times, not a 409 - so
    `connection_identity_map()` can rebuild a correct, complete mapping
    fresh on every worker boot from this function's return value alone;
    nothing needs to be persisted. The `ALREADY_REGISTERED`/409 handling
    below is kept as a defensive fallback for a genuine external conflict
    (e.g. the same name legitimately taken by a different, unrelated
    Caspian account), which the live test did not encounter.

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
                partial(client.connect_email, username=identity),
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
                    partial(client.connect_telegram, bot_token=settings.telegram_bot_token),
                )
        if "slack" in channels:
            results[(identity, "slack")] = _register_channel(
                identity, "slack",
                partial(client.install_slack, display_name=f"Sieve ({identity})"),
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
                    ),
                )

    return results


def connection_identity_map(
    results: dict[tuple[str, str], dict | str | None],
) -> dict[str, str]:
    """Build a `connection_id -> identity` map from `register_identities`'s
    return value, so `handler.py` can resolve which of Sieve's 3 identities
    an inbound `Message` belongs to via `Message.connection_id` (see
    `register_identities`'s docstring for why `Message.agent_id` can't be
    used for this).

    Rebuilt fresh from the live return value on every worker boot - no
    persistence needed, since registration was verified idempotent (see
    `register_identities`'s docstring). A channel whose result is
    `ALREADY_REGISTERED` or `None` (a genuine conflict, or blank secret)
    contributes nothing here; messages arriving on that connection won't
    resolve to an identity until it registers successfully.

    Raises `ValueError` if two different identities resolve to the same
    `connection_id` instead of silently letting the later one win - this is
    the exact shape of the live-observed sandbox bug (see the KNOWN
    LIMITATION note above): careers/email and internal/email both landing on
    one shared connection, which would otherwise silently misroute all of
    the losing identity's inbound mail to the winner.
    """
    mapping: dict[str, str] = {}
    for (identity, _channel), result in results.items():
        if not (isinstance(result, dict) and "id" in result):
            continue
        connection_id = result["id"]
        existing_identity = mapping.get(connection_id)
        if existing_identity is not None and existing_identity != identity:
            raise ValueError(
                f"connection_id {connection_id!r} resolved to both "
                f"{existing_identity!r} and {identity!r} - Caspian returned "
                "the same connection for two different Sieve identities "
                "(see the KNOWN LIMITATION note in this module); refusing "
                "to build an ambiguous connection -> identity mapping"
            )
        mapping[connection_id] = identity
    return mapping


def validate_identity_coverage(
    results: dict[tuple[str, str], dict | str | None],
    connection_map: dict[str, str],
) -> None:
    """Fail loudly if any identity that requires "email" (currently all 3 -
    see `IDENTITY_CHANNELS`) does not have exactly one resolved connection_id
    in `connection_map`.

    An `ALREADY_REGISTERED` (409) result is treated as unusable here, not as
    success: we don't retrieve the existing connection's id from the
    gateway (that would need a separate lookup call this module doesn't
    make - see the KNOWN LIMITATION note), so a 409'd email identity has no
    way to route inbound mail until it registers cleanly. Call this once at
    worker startup, after `connection_identity_map`, and let the
    `RuntimeError` propagate to stop the worker before `client.listen()` -
    starting up with incomplete email coverage means silently dropping or
    misrouting mail for whichever identity is missing.
    """
    unresolved = [
        identity
        for identity, channels in IDENTITY_CHANNELS.items()
        if "email" in channels
        and not (
            isinstance(results.get((identity, "email")), dict)
            and results[(identity, "email")]["id"] in connection_map
            and connection_map[results[(identity, "email")]["id"]] == identity
        )
    ]
    if unresolved:
        raise RuntimeError(
            "Incomplete email identity coverage - no resolved connection_id "
            f"mapping for: {unresolved}. Refusing to start listen() with "
            "identities that cannot reliably route inbound email."
        )


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
