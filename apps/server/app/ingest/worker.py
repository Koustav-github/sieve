import logging

from caspian_sdk import CommClient

from app.db.session import SessionLocal
from app.ingest.handler import build_on_message_handler
from app.ingest.identities import connection_identity_map, register_identities

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
    client.on_message(build_on_message_handler(SessionLocal, connection_identities))
    logger.info("Sieve ingestion worker listening...")
    client.listen()


if __name__ == "__main__":
    main()
