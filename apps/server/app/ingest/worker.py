import logging

from caspian_sdk import CommClient

from app.db.session import SessionLocal
from app.ingest.handler import build_on_message_handler
from app.ingest.identities import register_identities

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    client = CommClient()
    register_identities(client)
    client.on_message(build_on_message_handler(SessionLocal))
    logger.info("Sieve ingestion worker listening...")
    client.listen()


if __name__ == "__main__":
    main()
