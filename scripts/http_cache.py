"""Disk-backed, rate-limited HTTP client used by all source modules.

Single shared session per process. Each request is:

  1. Looked up in `.http_cache/{sha256(method:url:body)}` first.
  2. Otherwise fetched, with a 1 req/sec/host token bucket.
  3. robots.txt-checked once per host (cached).
  4. Sent with the SEC-required User-Agent (env: SEC_USER_AGENT).

Used by `sources/finance_database.py`, `sources/edgar.py`,
`sources/issuer_scraper.py`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.robotparser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("ASSET_PROFILES_CACHE_DIR", ".http_cache"))
DEFAULT_UA = (
    "Wealthfolio asset-profiles bot (opensource@wealthfolio.app) "
    "https://github.com/wealthfolio/asset-profiles"
)
MIN_INTERVAL_SEC = 1.0  # 1 req/sec/host


def _user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT") or os.environ.get("USER_AGENT") or DEFAULT_UA


class _RateLimiter:
    """Per-host token bucket: at most one request per MIN_INTERVAL_SEC seconds."""

    def __init__(self, min_interval: float = MIN_INTERVAL_SEC):
        self._min_interval = min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            prev = self._last.get(host, 0.0)
            wait_for = self._min_interval - (now - prev)
            if wait_for > 0:
                time.sleep(wait_for)
            self._last[host] = time.monotonic()


class HttpCache:
    def __init__(self, cache_dir: Path = CACHE_DIR, *, respect_robots: bool = True):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = _user_agent()
        self.session.headers["Accept-Encoding"] = "gzip, deflate"
        self._rate = _RateLimiter()
        self._robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._respect_robots = respect_robots

    # ---- public API ----------------------------------------------------

    def get(self, url: str, *, accept: Optional[str] = None, force: bool = False) -> bytes:
        return self._request("GET", url, accept=accept, force=force)

    def get_text(self, url: str, *, encoding: str = "utf-8", **kw) -> str:
        return self.get(url, **kw).decode(encoding, errors="replace")

    def get_json(self, url: str, **kw) -> dict | list:
        return json.loads(self.get_text(url, accept="application/json", **kw))

    # ---- internal ------------------------------------------------------

    def _request(self, method: str, url: str, *, accept: Optional[str], force: bool) -> bytes:
        key = self._cache_key(method, url, accept)
        cache_path = self.cache_dir / key
        if not force and cache_path.exists():
            return cache_path.read_bytes()

        host = urlparse(url).netloc
        if self._respect_robots and not self._robots_allows(url):
            raise PermissionError(f"robots.txt disallows {url}")

        self._rate.wait(host)
        headers = {}
        if accept:
            headers["Accept"] = accept
        log.debug("HTTP %s %s", method, url)
        resp = self.session.request(method, url, headers=headers, timeout=60)
        resp.raise_for_status()
        body = resp.content
        cache_path.write_bytes(body)
        return body

    def _cache_key(self, method: str, url: str, accept: Optional[str]) -> str:
        h = hashlib.sha256()
        h.update(method.encode())
        h.update(b"\x00")
        h.update(url.encode())
        if accept:
            h.update(b"\x00")
            h.update(accept.encode())
        return h.hexdigest()

    def _robots_allows(self, url: str) -> bool:
        parts = urlparse(url)
        host = parts.netloc
        if host not in self._robots:
            robots_url = f"{parts.scheme}://{host}/robots.txt"
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._rate.wait(host)
                resp = self.session.get(robots_url, timeout=15)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None  # type: ignore[assignment]
            except requests.RequestException:
                log.warning("robots.txt fetch failed for %s; assuming allowed", host)
                rp = None  # type: ignore[assignment]
            self._robots[host] = rp
        rp = self._robots[host]
        if rp is None:
            return True
        return rp.can_fetch(_user_agent(), url)


# Module-level singleton; tests can monkey-patch.
_default: Optional[HttpCache] = None


def default() -> HttpCache:
    global _default
    if _default is None:
        _default = HttpCache()
    return _default
