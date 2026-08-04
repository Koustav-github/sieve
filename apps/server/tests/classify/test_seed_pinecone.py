import pytest

from app.classify import seed_pinecone as seed_pinecone_module
from app.classify.seed import load_seed_data as real_load_seed_data


class _FakeClient:
    def __init__(self):
        self.embedded = []
        self.upserted = []

    def embed_passages(self, texts):
        self.embedded.append(texts)
        # one fixed-length fake vector per text, so centroid math is exercisable
        return [[1.0, 2.0] for _ in texts]

    def upsert_centroid(self, bucket_name, vector):
        self.upserted.append((bucket_name, vector))


def test_main_raises_when_pinecone_api_key_is_blank(monkeypatch):
    monkeypatch.setattr(seed_pinecone_module.settings, "pinecone_api_key", "")

    with pytest.raises(RuntimeError, match="PINECONE_API_KEY"):
        seed_pinecone_module.main()


def test_main_embeds_and_upserts_one_centroid_per_bucket_with_exemplars(monkeypatch, tmp_path):
    monkeypatch.setattr(seed_pinecone_module.settings, "pinecone_api_key", "test-key")
    fake_client = _FakeClient()
    monkeypatch.setattr(seed_pinecone_module, "build_pinecone_client", lambda: fake_client)

    seed_path = tmp_path / "seed.json"
    seed_path.write_text(
        """
        {
          "buckets": [
            {"name": "with_exemplars", "description": "d", "exemplars": ["a phrase", "another phrase"]},
            {"name": "no_exemplars", "description": "d"}
          ],
          "rules": []
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        seed_pinecone_module, "load_seed_data", lambda: real_load_seed_data(seed_path)
    )

    seed_pinecone_module.main()

    assert fake_client.embedded == [["a phrase", "another phrase"]]
    assert fake_client.upserted == [("with_exemplars", [1.0, 2.0])]


def test_centroid_averages_vectors_elementwise():
    result = seed_pinecone_module._centroid([[1.0, 3.0], [3.0, 5.0]])

    assert result == [2.0, 4.0]
