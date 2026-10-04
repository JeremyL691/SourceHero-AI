from app.ingestion.chunking import chunk_text, clean_text, estimate_tokens, sha256_text


def test_clean_text_normalizes_whitespace():
    assert clean_text(" hello\n\n world\t ") == "hello world"


def test_chunk_text_uses_overlap():
    text = " ".join(f"word{i}" for i in range(30))
    chunks = chunk_text(text, target_words=10, overlap_words=2)
    assert len(chunks) == 4
    assert chunks[0].split()[-2:] == chunks[1].split()[:2]


def test_hash_and_token_estimate_are_stable():
    assert sha256_text("a", "b") == sha256_text("a", "b")
    assert estimate_tokens("abcd" * 10) >= 10
