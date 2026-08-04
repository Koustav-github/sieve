from pinecone import Pinecone

from app.core.config import settings

EMBED_MODEL = "multilingual-e5-large"


class PineconeIndexClient:
    """Real `app.classify.semantic.SemanticIndexClient` backed by the
    Pinecone SDK: embeds via Pinecone's hosted Inference API, queries the
    configured index for the nearest bucket centroid vector. Centroids are
    upserted by `app/classify/seed_pinecone.py`, keyed by bucket name in each
    match's metadata."""

    def __init__(self, pc: Pinecone, index, embed_model: str = EMBED_MODEL):
        self._pc = pc
        self._index = index
        self._embed_model = embed_model

    def embed(self, text: str) -> list[float]:
        result = self._pc.inference.embed(
            model=self._embed_model,
            inputs=[text],
            parameters={"input_type": "query", "truncate": "END"},
        )
        return list(result[0].values)

    def query(self, vector: list[float], top_k: int) -> list[tuple[str, float]]:
        response = self._index.query(vector=vector, top_k=top_k, include_metadata=True)
        return [(match.metadata["bucket"], match.score) for match in response.matches]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Used only by `seed_pinecone.py` to embed a bucket's exemplar
        phrases before averaging them into one centroid - `input_type`
        differs from `embed()`'s ("passage" vs "query"), matching Pinecone's
        own guidance for indexed content vs. search queries."""
        result = self._pc.inference.embed(
            model=self._embed_model,
            inputs=texts,
            parameters={"input_type": "passage", "truncate": "END"},
        )
        return [list(item.values) for item in result]

    def upsert_centroid(self, bucket_name: str, vector: list[float]) -> None:
        self._index.upsert(
            vectors=[
                {
                    "id": f"bucket-{bucket_name}",
                    "values": vector,
                    "metadata": {"bucket": bucket_name},
                }
            ]
        )


def build_pinecone_client() -> PineconeIndexClient | None:
    """Returns `None` when `PINECONE_API_KEY` isn't configured - the
    semantic-matching L1 signal is optional infra, not required to run the
    classification cascade (unlike Anthropic, which L3 always needs)."""
    if not settings.pinecone_api_key:
        return None
    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.index(name=settings.pinecone_index)
    return PineconeIndexClient(pc=pc, index=index)
