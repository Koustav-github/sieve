from app.classify.semantic import semantic_match


class _FakeClient:
    def __init__(self, results):
        self.results = results
        self.embed_calls = []

    def embed(self, text):
        self.embed_calls.append(text)
        return [0.1, 0.2, 0.3]

    def query(self, vector, top_k):
        return self.results


class _RaisingClient:
    def embed(self, text):
        raise RuntimeError("Pinecone is down")

    def query(self, vector, top_k):
        raise AssertionError("should not be called if embed() raised")


def test_returns_best_match_above_threshold():
    client = _FakeClient([("customer_support", 0.91), ("vendor_invoice", 0.4)])

    result = semantic_match(client, "my order never arrived")

    assert result is not None
    assert result.bucket_name == "customer_support"
    assert result.score == 0.91
    assert client.embed_calls == ["my order never arrived"]


def test_returns_none_below_threshold():
    client = _FakeClient([("customer_support", 0.5)])

    result = semantic_match(client, "hello")

    assert result is None


def test_returns_none_when_no_results():
    client = _FakeClient([])

    result = semantic_match(client, "hello")

    assert result is None


def test_returns_none_and_does_not_raise_on_client_error():
    result = semantic_match(_RaisingClient(), "hello")

    assert result is None
