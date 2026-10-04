from app.ingestion.rss import parse_rss_bytes

SAMPLE_RSS = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>Vector Databases</title>
      <link>https://example.com/vector</link>
      <description>Vector search helps retrieval augmented generation.</description>
      <pubDate>Sun, 24 May 2026 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


def test_parse_rss_bytes_extracts_entries():
    docs = parse_rss_bytes(SAMPLE_RSS)
    assert len(docs) == 1
    assert docs[0].title == "Vector Databases"
    assert "retrieval augmented generation" in docs[0].clean_text
    assert docs[0].url == "https://example.com/vector"


def test_parse_rss_full_article_fetch_falls_back_to_summary(monkeypatch):
    def blocked_article(url):
        raise RuntimeError("403 Client Error: Forbidden")

    monkeypatch.setattr("app.ingestion.rss.fetch_webpage_text", blocked_article)

    docs = parse_rss_bytes(SAMPLE_RSS, fetch_full_articles=True)

    assert len(docs) == 1
    assert docs[0].title == "Vector Databases"
    assert "retrieval augmented generation" in docs[0].clean_text
