"""WebFetcher — the web URL fetch deep module.

Fetches one page over HTTP and returns its main content as markdown, so the assistant can
dig into references. The security-relevant logic (the URL scheme check, manual redirect
following, content-type/size handling) lives behind a narrow interface tested directly with
an injected fake HTTP client + fake extractor — no network, no trafilatura — mirroring
KnowledgeGraph/Workspace. A thin surface (build_tools + FETCH_GUIDANCE) phrases it for the
model. This is the codebase's fourth tool shape: an in-process, network-egress, auto-run
function tool.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_BYTES = 2_000_000      # read ceiling on a response body
MAX_REDIRECTS = 5          # manual redirect hops
CONTENT_CAP = 100_000      # chars of extracted markdown surfaced to the model (local; cf. READ_CAP)
TIMEOUT_S = 20.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

_ALLOWED_SCHEMES = ("http", "https")


@dataclass
class HttpResponse:
    status_code: int
    headers: dict[str, str]   # lowercased keys
    url: str                  # the URL of this hop
    body: bytes
    truncated: bool = False


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HTML_TYPES = ("text/html", "application/xhtml+xml")


def _extract_title(html: str) -> str | None:
    m = _TITLE_RE.search(html)
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title or None


class WebFetcher:
    def __init__(self, *, client=None, extractor=None) -> None:
        self._client = client                 # lazily defaulted in Task 5
        self._extractor = extractor           # lazily defaulted in Task 5

    async def fetch(self, url: str) -> str:
        err = _safe_url(url)
        if err is not None:
            return err
        resp = await self._client.fetch_once(url, headers={"User-Agent": USER_AGENT},
                                             max_bytes=MAX_BYTES)
        ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        html = resp.body.decode("utf-8", errors="replace")
        markdown = await asyncio.to_thread(self._extractor, html, resp.url)
        markdown = (markdown or "")[:CONTENT_CAP]
        title = _extract_title(html)
        title_attr = f' title="{title}"' if title else ""
        return f'<fetched_url url="{resp.url}"{title_attr}>\n{markdown}\n</fetched_url>'


def _safe_url(url: str) -> str | None:
    """Return an error message if ``url`` is unsafe/malformed, else None.

    The one guard every request and redirect hop passes through. Scheme allowlist only —
    no private-IP/localhost block (single-user local app; see spec §C). The scheme check
    stops the 'web' tool from becoming a file:// local-read primitive.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return f"Not a valid URL: {url}"
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"Only http/https URLs can be fetched (got {parts.scheme or 'no'}-scheme URL)."
    if not parts.hostname:
        return f"URL has no host: {url}"
    return None
