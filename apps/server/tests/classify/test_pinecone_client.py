from unittest.mock import MagicMock

from app.classify import pinecone_client as pinecone_client_module
from app.classify.pinecone_client import PineconeIndexClient


def test_build_pinecone_client_returns_none_when_key_unset(monkeypatch):
    monkeypatch.setattr(pinecone_client_module.settings, "pinecone_api_key", "")

    result = pinecone_client_module.build_pinecone_client()

    assert result is None


def test_build_pinecone_client_constructs_client_when_key_set(monkeypatch):
    monkeypatch.setattr(pinecone_client_module.settings, "pinecone_api_key", "test-key")
    monkeypatch.setattr(pinecone_client_module.settings, "pinecone_index", "my-index")
    fake_pc_instance = MagicMock()
    fake_index = MagicMock()
    fake_pc_instance.index.return_value = fake_index
    fake_pc_cls = MagicMock(return_value=fake_pc_instance)
    monkeypatch.setattr(pinecone_client_module, "Pinecone", fake_pc_cls)

    result = pinecone_client_module.build_pinecone_client()

    fake_pc_cls.assert_called_once_with(api_key="test-key")
    fake_pc_instance.index.assert_called_once_with(name="my-index")
    assert isinstance(result, PineconeIndexClient)


def test_embed_returns_first_result_values():
    fake_pc = MagicMock()
    fake_embedding = MagicMock()
    fake_embedding.values = [0.1, 0.2, 0.3]
    fake_pc.inference.embed.return_value = [fake_embedding]
    client = PineconeIndexClient(pc=fake_pc, index=MagicMock())

    result = client.embed("hello")

    assert result == [0.1, 0.2, 0.3]
    _, kwargs = fake_pc.inference.embed.call_args
    assert kwargs["parameters"]["input_type"] == "query"


def test_query_returns_bucket_score_pairs():
    fake_index = MagicMock()
    match1 = MagicMock(metadata={"bucket": "customer_support"}, score=0.9)
    match2 = MagicMock(metadata={"bucket": "vendor_invoice"}, score=0.3)
    fake_index.query.return_value = MagicMock(matches=[match1, match2])
    client = PineconeIndexClient(pc=MagicMock(), index=fake_index)

    result = client.query(vector=[0.1, 0.2], top_k=2)

    assert result == [("customer_support", 0.9), ("vendor_invoice", 0.3)]


def test_upsert_centroid_upserts_one_vector_keyed_by_bucket_name():
    fake_index = MagicMock()
    client = PineconeIndexClient(pc=MagicMock(), index=fake_index)

    client.upsert_centroid("customer_support", [0.1, 0.2])

    _, kwargs = fake_index.upsert.call_args
    assert kwargs["vectors"] == [
        {"id": "bucket-customer_support", "values": [0.1, 0.2], "metadata": {"bucket": "customer_support"}}
    ]
