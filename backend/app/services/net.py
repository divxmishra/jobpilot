"""Polite HTTP fetching: identifies itself, honours robots.txt for HTML pages,
rate-limits per host, never attempts to bypass CAPTCHA/logins/bot protection."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from ..config import settings

_host_last: dict[str, float] = {}
_robots: dict[str, tuple[float, RobotFileParser | None]] = {}
_lock = threading.Lock()
MIN_INTERVAL = 1.0  # seconds between requests to the same host
MAX_BYTES = 3_000_000       # HTML pages
MAX_JSON_BYTES = 30_000_000  # official board APIs can return large listings

BLOCK_MARKERS = ("captcha", "cf-challenge", "are you a robot", "access denied", "verify you are human",
                 "unusual traffic", "enable javascript and cookies to continue")


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    text: str
    content_type: str
    error: str | None = None
    blocked: bool = False
    robots_disallowed: bool = False


def _wait_host(host: str) -> None:
    with _lock:
        last = _host_last.get(host, 0)
        delay = MIN_INTERVAL - (time.monotonic() - last)
        _host_last[host] = max(time.monotonic(), last + MIN_INTERVAL)
    if delay > 0:
        time.sleep(delay)


def robots_allowed(url: str, client: httpx.Client | None = None) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    cached = _robots.get(base)
    if cached and time.time() - cached[0] < 3600:
        rp = cached[1]
    else:
        rp = RobotFileParser()
        try:
            c = client or httpx.Client(timeout=10, headers={"User-Agent": settings.user_agent}, follow_redirects=True)
            r = c.get(base + "/robots.txt")
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            elif r.status_code in (401, 403):
                rp.disallow_all = True
            else:
                rp = None  # no robots.txt => allowed
        except httpx.HTTPError:
            rp = None
        _robots[base] = (time.time(), rp)
    return True if rp is None else rp.can_fetch(settings.user_agent, url)


def fetch(url: str, *, check_robots: bool = True, accept: str = "text/html,application/json;q=0.9,*/*;q=0.5",
          max_bytes: int = MAX_BYTES) -> FetchResult:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return FetchResult(url, url, None, "", "", error="Invalid URL (must start with http:// or https://)")
    host = p.hostname or ""
    if host in ("localhost", "127.0.0.1", "0.0.0.0") or host.endswith(".local") or host.startswith("169.254.") \
            or host.startswith("10.") or host.startswith("192.168."):
        if not settings.env == "test":
            return FetchResult(url, url, None, "", "", error="Private/internal addresses are not allowed")
    headers = {"User-Agent": settings.user_agent, "Accept": accept, "Accept-Language": "en"}
    try:
        with httpx.Client(timeout=settings.http_timeout, headers=headers, follow_redirects=True, max_redirects=5) as c:
            if check_robots and not robots_allowed(url, c):
                return FetchResult(url, url, None, "", "", robots_disallowed=True,
                                   error="The site's robots.txt disallows automated access to this page")
            _wait_host(host)
            r = c.get(url)
            body = r.content[:max_bytes].decode(r.encoding or "utf-8", errors="replace")
            ctype = r.headers.get("content-type", "")
            low = body[:20000].lower()
            blocked = r.status_code in (403, 429, 503) and any(m in low for m in BLOCK_MARKERS)
            return FetchResult(url, str(r.url), r.status_code, body, ctype, blocked=blocked)
    except httpx.TimeoutException:
        return FetchResult(url, url, None, "", "", error="Timed out while loading the page")
    except httpx.HTTPError as e:
        return FetchResult(url, url, None, "", "", error=f"Network error: {type(e).__name__}")


def fetch_json(url: str) -> tuple[int | None, object | None, str | None]:
    """For official public job-board APIs (Greenhouse/Lever/Ashby/Remotive)."""
    r = fetch(url, check_robots=False, accept="application/json", max_bytes=MAX_JSON_BYTES)
    if r.error:
        return None, None, r.error
    if r.status != 200:
        return r.status, None, f"HTTP {r.status}"
    try:
        import json
        return r.status, json.loads(r.text), None
    except ValueError:
        return r.status, None, "Response was not valid JSON"
