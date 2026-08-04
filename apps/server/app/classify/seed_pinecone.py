"""One-off script: embeds each bucket's exemplar phrases from seed_data.json
and upserts one centroid vector per bucket into the configured Pinecone
index, so `app.classify.semantic.semantic_match` has something to query
against.

Not run automatically at worker startup (unlike `seed.seed_buckets_and_rules`)
- Pinecone is optional infra, and re-embedding on every restart would burn
Inference API calls for no benefit once the centroids already exist. Re-run
manually after editing exemplars in seed_data.json:

    uv run python -m app.classify.seed_pinecone
"""

import logging

from app.classify.pinecone_client import build_pinecone_client
from app.classify.seed import load_seed_data
from app.core.config import settings

logger = logging.getLogger(__name__)


def _centroid(vectors: list[list[float]]) -> list[float]:
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]


def main() -> None:
    if not settings.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is not set - cannot seed the semantic index")

    client = build_pinecone_client()
    data = load_seed_data()

    seeded = 0
    for bucket in data["buckets"]:
        exemplars = bucket.get("exemplars") or []
        if not exemplars:
            logger.warning("Bucket %r has no exemplars, skipping", bucket["name"])
            continue
        vectors = client.embed_passages(exemplars)
        client.upsert_centroid(bucket["name"], _centroid(vectors))
        seeded += 1

    logger.info("Upserted %d bucket centroids to Pinecone index %r", seeded, settings.pinecone_index)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
