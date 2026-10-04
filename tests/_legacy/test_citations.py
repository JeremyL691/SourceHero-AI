from app.retrieval.search import SearchResult
from app.services.citations import answer_with_citations, citation_label, format_sources


def test_citation_label_for_web_source():
    hit = SearchResult(
        chunk_id=1,
        document_id=2,
        source_id=3,
        title="Vector Databases",
        source_name="Feed",
        source_type="rss",
        url="https://example.com/vector",
        local_path=None,
        score=1.0,
        snippet="Vector databases support retrieval.",
        chunk_text="Vector databases support retrieval.",
        metadata={},
    )
    assert citation_label(hit, 1) == "[1] Vector Databases - https://example.com/vector"
    assert "Sources:" in format_sources([hit])
    assert "[1]" in answer_with_citations("What supports retrieval?", [hit])


def test_answer_without_hits_refuses():
    assert "not have enough indexed evidence" in answer_with_citations("Unsupported?", [])
