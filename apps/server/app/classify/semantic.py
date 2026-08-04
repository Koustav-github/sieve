import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

# Below this cosine-similarity score, a semantic "match" is treated as noise
# rather than a real signal - the rule-based L1 signals and the L3 fallback
# still get a chance.
SEMANTIC_SCORE_THRESHOLD = 0.75


class SemanticIndexClient(Protocol):
    """Minimal interface `semantic_match()` depends on. Real implementation
    is `app.classify.pinecone_client.PineconeIndexClient`; tests use a fake
    with the same two methods (same pattern as the L3/subject-extraction
    `Runnable` fakes elsewhere in this package)."""

    def embed(self, text: str) -> list[float]: ...

    def query(self, vector: list[float], top_k: int) -> list[tuple[str, float]]:
        """Returns (bucket_name, score) pairs, best match first."""
        ...


@dataclass
class SemanticMatch:
    bucket_name: str
    score: float


def semantic_match(client: SemanticIndexClient, text: str) -> SemanticMatch | None:
    """Embeds `text` and queries `client` for the nearest bucket centroid.

    Returns `None` if no result clears SEMANTIC_SCORE_THRESHOLD, or if the
    client raises for any reason (network error, timeout, misconfigured
    index) - a semantic-signal failure must only skip this signal, the same
    error posture as L1's other signals, never crash the classification
    graph."""
    try:
        vector = client.embed(text)
        results = client.query(vector=vector, top_k=1)
    except Exception as exc:  # noqa: BLE001 - a semantic-client failure must only skip this signal, never crash L1
        logger.warning("Semantic match skipped (client error): %s", exc)
        return None

    if not results:
        return None

    bucket_name, score = results[0]
    if score < SEMANTIC_SCORE_THRESHOLD:
        return None
    return SemanticMatch(bucket_name=bucket_name, score=score)
