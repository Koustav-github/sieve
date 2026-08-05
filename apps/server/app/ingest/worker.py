import logging
from concurrent.futures import ThreadPoolExecutor

from caspian_sdk import CommClient

from app.db.session import SessionLocal
from app.ingest.handler import build_on_message_handler
from app.relay.llm import build_relay_llm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RELAY_EXECUTOR_WORKERS = 4


def main() -> None:
    client = CommClient()

    # The one shared, bot-owned connection every personal-chat relay sends
    # from (Global Constraints) - a dedicated identity, not tied to any one
    # department. Uses connect_email() the same way v1's identities.py did
    # for its 3 fixed identities, just with a fixed username instead of a
    # per-identity one.
    relay_sender_connection = client.connect_email(username="relay")
    relay_sender_connection_id = relay_sender_connection["id"]
    logger.info("Relay-sender connection: %r", relay_sender_connection)

    executor = ThreadPoolExecutor(max_workers=RELAY_EXECUTOR_WORKERS, thread_name_prefix="sieve-relay")
    relay_llm = build_relay_llm()

    client.on_message(
        build_on_message_handler(
            SessionLocal,
            relay_llm,
            executor,
            client,
            relay_sender_connection_id,
        )
    )
    logger.info("Sieve ingestion worker listening...")
    client.listen()


if __name__ == "__main__":
    main()
