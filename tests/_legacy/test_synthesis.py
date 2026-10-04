from app.retrieval.search import SearchResult
from app.services.synthesis import synthesize_with_openai


class _FakeResponses:
    def create(self, model, input):
        assert model == "test-model"
        assert "Question:" in input
        return type("Response", (), {"output_text": "The source says retrieval matters [1]."})()


class _FakeClient:
    responses = _FakeResponses()


def _hit():
    return SearchResult(
        chunk_id=1,
        document_id=2,
        source_id=3,
        title="Retrieval Notes",
        source_name="Notebook",
        source_type="webpage",
        url="https://example.com",
        local_path=None,
        score=1.0,
        snippet="retrieval matters",
        chunk_text="retrieval matters for grounded answers",
        metadata={},
    )


def test_synthesis_returns_none_without_config():
    assert synthesize_with_openai("What matters?", [_hit()], None, "model") is None
    assert synthesize_with_openai("What matters?", [_hit()], "key", None) is None


def test_synthesis_uses_mocked_client_and_appends_sources():
    answer = synthesize_with_openai(
        "What matters?",
        [_hit()],
        "key",
        "test-model",
        client_factory=lambda api_key: _FakeClient(),
    )

    assert "retrieval matters [1]" in answer
    assert "Sources:" in answer
    assert "Retrieval Notes" in answer
