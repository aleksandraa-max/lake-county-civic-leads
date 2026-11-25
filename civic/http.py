"""Polite HTTP: one request per host per `delay` seconds, retries, size cap.

Tests swap in a FakeFetcher with the same `get` signature.
"""
from __future__ import annotations

import os
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

CONTACT = os.environ.get("CONTACT_EMAIL", "set-CONTACT_EMAIL@example.com")
BOT_NAME = "LakeCountyCivicArchive"
# Standard "compatible" bot format: some village firewalls reject User-Agents that
# don't start with Mozilla/5.0. It still names the bot and the contact address.
USER_AGENT = f"Mozilla/5.0 (compatible; {BOT_NAME}/0.1; public-records research; contact {CONTACT})"
MAX_BYTES = 60 * 1024 * 1024  # skip files over 60 MB (giant scanned packets)


@dataclass
class Resp:
    url: str
    status: int
    content: bytes
    headers: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        enc = "utf-8"
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype:
            enc = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        return self.content.decode(enc, errors="replace")

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


class Fetcher:
    def __init__(self, delay: float = 1.0, timeout: float = 30.0, retries: int = 2, respect_robots: bool = True):
        self.respect_robots = respect_robots
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self._last: dict[str, float] = {}
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.requests_made = 0

    def _wait(self, host: str) -> None:
        last = self._last.get(host, 0.0)
        gap = time.monotonic() - last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        """Honor robots.txt (fetched once per host; unreachable robots.txt = allowed)."""
        p = urlparse(url)
        key = f"{p.scheme}://{p.netloc}"
        if key not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(key + "/robots.txt", timeout=10)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            self._robots[key] = rp
        return self._robots[key].can_fetch(BOT_NAME, url)

    def get(self, url: str, method: str = "GET", **kw) -> Resp:
        host = urlparse(url).netloc
        if self.respect_robots and not self.allowed(url):
            return Resp(url, 999, b"blocked by robots.txt", {})
        err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait(host)
            try:
                self.requests_made += 1
                r = self.s.request(method, url, timeout=self.timeout, stream=True, allow_redirects=True, **kw)
                size = int(r.headers.get("content-length") or 0)
                if size > MAX_BYTES:
                    r.close()
                    return Resp(r.url, 413, b"", {k.lower(): v for k, v in r.headers.items()})
                buf = bytearray()
                for chunk in r.iter_content(65536):
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        r.close()
                        return Resp(r.url, 413, b"", {k.lower(): v for k, v in r.headers.items()})
                resp = Resp(r.url, r.status_code, bytes(buf), {k.lower(): v for k, v in r.headers.items()})
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(5 * (attempt + 1))
                    continue
                return resp
            except requests.RequestException as e:
                err = e
                time.sleep(3 * (attempt + 1))
        return Resp(url, 0, str(err).encode(), {})
