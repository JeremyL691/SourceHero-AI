"""v0.4.1 B4 — _sanitize_error returns short, readable text without tracebacks."""

from __future__ import annotations

from app.services.pipeline import _sanitize_error


def test_403_is_recognized():
    exc = RuntimeError("403 Client Error: Forbidden for url: https://example.com/secret")
    assert "403" in _sanitize_error(exc)
    assert "Forbidden" not in _sanitize_error(exc) or "blocks" in _sanitize_error(exc)


def test_timeout_is_recognized():
    exc = RuntimeError("HTTPSConnectionPool(host='x.com', port=443): Read timed out.")
    assert "timeout" in _sanitize_error(exc).lower() or "timed out" in _sanitize_error(exc).lower()


def test_dns_failure_recognized():
    exc = OSError("[Errno -3] nodename nor servname provided, or not known")
    out = _sanitize_error(exc)
    assert "DNS" in out or "dns" in out


def test_short_output_no_traceback():
    """Even a verbose exception should yield <= ~250 chars and no newline-separated stack."""
    exc = RuntimeError("a" * 5000)
    out = _sanitize_error(exc)
    assert len(out) < 260
    assert "\n" not in out


def test_unknown_exception_falls_back_with_classname():
    class WeirdProblem(Exception):
        pass

    out = _sanitize_error(WeirdProblem("something broke"))
    assert "WeirdProblem" in out or "something broke" in out
