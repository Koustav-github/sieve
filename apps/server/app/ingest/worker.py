import logging
from concurrent.futures import ThreadPoolExecutor

from caspian_sdk import CommClient

from app.db.session import SessionLocal
from app.ingest.handler import build_on_message_handler
from app.ingest.identities import (
    connection_identity_map,
    register_identities,
    validate_identity_coverage,
)
from app.relay.llm import build_relay_llm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Relay dispatch/LLM detection runs off the ingest listen() loop (see
# handler._relay_and_record) so latency never blocks message intake;
# bounded to avoid unbounded thread growth under a burst of messages.
RELAY_EXECUTOR_WORKERS = 4


def main() -> None:
    client = CommClient()

    # register_identities() is non-fatal per channel (see its docstring for
    # the 409-on-restart / blank-secret reasoning) so a single bad channel
    # can never stop us from reaching client.listen() below. The only
    # explicit, intentional fatal case is when literally every channel
    # failed to register (and none were already registered from a previous
    # run) - that means ingestion cannot receive anything on any channel, so
    # there is no point starting the listener.
    results = register_identities(client)
    for key, result in results.items():
        logger.info("identity registration result %s -> %r", key, result)
    if results and all(result is None for result in results.values()):
        raise RuntimeError(
            "register_identities: every channel failed to register "
            "(see warnings above) - refusing to start listen()"
        )

    connection_identities = connection_identity_map(results)
    logger.info("connection -> identity map: %r", connection_identities)
    validate_identity_coverage(results, connection_identities)

    # Relay dispatch always goes out over email (the one channel all 3
    # identities share - see IDENTITY_CHANNELS in identities.py), using each
    # identity's own already-registered connection as both the "send from"
    # (source) and "send to" (target) address - see app.relay.dispatcher.
    identity_email_connections = {
        identity: result
        for (identity, channel), result in results.items()
        if channel == "email" and isinstance(result, dict)
    }

    executor = ThreadPoolExecutor(max_workers=RELAY_EXECUTOR_WORKERS, thread_name_prefix="sieve-relay")
    relay_llm = build_relay_llm()

    client.on_message(
        build_on_message_handler(
            SessionLocal,
            connection_identities,
            relay_llm,
            executor,
            client,
            identity_email_connections,
        )
    )
    logger.info("Sieve ingestion worker listening...")
    client.listen()


if __name__ == "__main__":
    main()
