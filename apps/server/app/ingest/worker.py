import logging

from caspian_sdk import CommClient

from app.classify.graph import build_classification_graph
from app.classify.llm import build_l3_llm, build_subject_extraction_llm
from app.classify.seed import seed_buckets_and_rules
from app.core.config import settings
from app.db.session import SessionLocal
from app.ingest.handler import build_on_message_handler
from app.ingest.identities import (
    connection_identity_map,
    register_identities,
    validate_identity_coverage,
)

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
    validate_identity_coverage(results, connection_identities)

    db = SessionLocal()
    try:
        seed_buckets_and_rules(db)
    finally:
        db.close()

    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set - refusing to start with no working "
            "classification"
        )

    classification_graph = build_classification_graph(
        SessionLocal, build_l3_llm(), build_subject_extraction_llm()
    )

    client.on_message(
        build_on_message_handler(SessionLocal, connection_identities, classification_graph)
    )
    logger.info("Sieve ingestion worker listening...")
    client.listen()


if __name__ == "__main__":
    main()
