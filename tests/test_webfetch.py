"""WebFetcher is the test surface for URL fetching: an injected fake HTTP client and a
fake extractor let us assert behavior with no network and no trafilatura."""

from __future__ import annotations

from llamatui.webfetch import _safe_url, HttpResponse, WebFetcher


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


class FakeClient:
    """Serves canned HttpResponses in order; records requested URLs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[str] = []
        self.closed = False

    async def fetch_once(self, url, *, headers, max_bytes):
        self.requests.append(url)
        resp = self._responses.pop(0)
        # Carry the requested URL through unless the canned response pins its own.
        return HttpResponse(resp.status_code, resp.headers, resp.url or url, resp.body, resp.truncated)

    async def aclose(self):
        self.closed = True


def _html_resp(html: str, url: str = "", status: int = 200, ctype: str = "text/html"):
    return HttpResponse(status, {"content-type": ctype}, url, html.encode("utf-8"))


def fake_extractor(markdown):
    return lambda html, url: markdown


async def test_fetch_returns_markdown_envelope():
    html = "<html><head><title>Hello</title></head><body><p>Hi</p></body></html>"
    fetcher = WebFetcher(client=FakeClient([_html_resp(html, url="https://ex.com/a")]),
                         extractor=fake_extractor("# Hi\n\nbody text"))
    out = await fetcher.fetch("https://ex.com/a")
    assert '<fetched_url url="https://ex.com/a" title="Hello">' in out
    assert "# Hi" in out
    assert out.rstrip().endswith("</fetched_url>")


async def test_fetch_rejects_unsafe_url_without_calling_client():
    client = FakeClient([])
    out = await WebFetcher(client=client, extractor=fake_extractor("x")).fetch("file:///etc/passwd")
    assert "http" in out.lower()
    assert client.requests == []   # never left the machine


def _redirect(location: str, status: int = 302):
    return HttpResponse(status, {"location": location}, "", b"")


async def test_fetch_follows_redirects_to_final_url():
    html = "<html><title>End</title><body>x</body></html>"
    client = FakeClient([
        _redirect("https://ex.com/2"),
        _html_resp(html, url="https://ex.com/2"),
    ])
    out = await WebFetcher(client=client, extractor=fake_extractor("done")).fetch("https://ex.com/1")
    assert 'url="https://ex.com/2"' in out
    assert client.requests == ["https://ex.com/1", "https://ex.com/2"]


async def test_fetch_rejects_redirect_to_unsafe_scheme():
    client = FakeClient([_redirect("file:///etc/passwd")])
    out = await WebFetcher(client=client, extractor=fake_extractor("x")).fetch("https://ex.com/1")
    assert "http" in out.lower()


async def test_fetch_stops_after_max_redirects():
    # 6 hops of redirect → exceeds MAX_REDIRECTS (5).
    client = FakeClient([_redirect(f"https://ex.com/{i}") for i in range(2, 9)])
    out = await WebFetcher(client=client, extractor=fake_extractor("x")).fetch("https://ex.com/1")
    assert "too many redirects" in out.lower()
