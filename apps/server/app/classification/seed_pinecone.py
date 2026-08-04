"""One-off script: embed each fine bucket's exemplar phrases via Pinecone's
Inference API, average them into one centroid per bucket, and upsert into
the configured Pinecone index.

This script upserts into an EXISTING index - it does not create one. Before
running it for the first time, create a Pinecone index matching
`settings.pinecone_index` with dimension 1024 and cosine similarity, to
match the `multilingual-e5-large` embedding model used here.

Run manually after editing app/classification/buckets.py:
    uv run python -m app.classification.seed_pinecone
"""

import logging
from typing import Any

from app.classification.buckets import FINE_BUCKETS
from app.classification.clients import PINECONE_EMBED_MODEL, build_pinecone_client
from app.core.config import settings

logger = logging.getLogger(__name__)


def embed_exemplars(pinecone_client: Any, texts: list[str]) -> list[list[float]]:
    response = pinecone_client.inference.embed(
        model=PINECONE_EMBED_MODEL,
        inputs=texts,
        parameters={"input_type": "passage", "truncate": "END"},
    )
    return [item["values"] for item in response]


def average_vector(vectors: list[list[float]]) -> list[float]:
    length = len(vectors[0])
    return [sum(vector[i] for vector in vectors) / len(vectors) for i in range(length)]


def seed() -> None:
    pinecone_client = build_pinecone_client()
    if pinecone_client is None:
        raise RuntimeError("PINECONE_API_KEY must be set to run seeding")

    index = pinecone_client.Index(settings.pinecone_index)

    vectors_to_upsert = []
    for coarse_bucket, fine_buckets in FINE_BUCKETS.items():
        for bucket in fine_buckets:
            embeddings = embed_exemplars(pinecone_client, bucket.exemplars)
            centroid = average_vector(embeddings)
            vectors_to_upsert.append(
                {
                    "id": f"{coarse_bucket}:{bucket.name}",
                    "values": centroid,
                    "metadata": {"coarse_bucket": coarse_bucket, "fine_bucket": bucket.name},
                }
            )

    index.upsert(vectors=vectors_to_upsert)
    logger.info(
        "Seeded %d bucket centroids into Pinecone index %s",
        len(vectors_to_upsert),
        settings.pinecone_index,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed()
