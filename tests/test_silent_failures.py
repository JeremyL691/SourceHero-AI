"""Swallowed exceptions leave a trace in the log (REVIEW DOC-11)."""

from __future__ import annotations

import logging

from app.ingestion.rss import _parse_time


def test_unparseable_feed_timestamp_logs_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert _parse_time("not a timestamp") is None
    messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("unparseable feed timestamp" in message for message in messages), messages


def test_parseable_feed_timestamp_logs_nothing(caplog):
    # The RFC 822 branch fails on ISO input by design; that expected fallback must stay silent.
    with caplog.at_level(logging.WARNING):
        assert _parse_time("2024-01-02T03:04:05Z") is not None
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
