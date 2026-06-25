"""WebFetcher is the test surface for URL fetching: an injected fake HTTP client and a
fake extractor let us assert behavior with no network and no trafilatura."""

from __future__ import annotations

from llamatui.webfetch import _safe_url


def test_safe_url_allows_http_and_https():
    assert _safe_url("http://example.com") is None
    assert _safe_url("https://example.com/page?q=1") is None


def test_safe_url_allows_localhost_and_private():
    # No SSRF blocklist: a single-user local app may read its own services (spec §C).
    assert _safe_url("http://localhost:8080") is None
    assert _safe_url("http://127.0.0.1:3000/docs") is None
    assert _safe_url("http://192.168.1.10") is None


def test_safe_url_rejects_non_http_schemes():
    for bad in ("file:///etc/passwd", "data:text/html,hi", "ftp://x/y", "gopher://x"):
        msg = _safe_url(bad)
        assert msg is not None and "http" in msg.lower()


def test_safe_url_rejects_missing_host():
    assert _safe_url("http://") is not None
    assert _safe_url("notaurl") is not None
