"""News: Google News RSS per town (headline, link, publisher, date only — no
article text is stored) plus each village's own news feed when it has one."""
from __future__ import annotations

import re
from urllib.parse import quote

import feedparser

from .classify import tags_for

GOOGLE = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def google_news_url(town: dict) -> str:
    name = town["name"]
    if town["kind"] == "county":
        q = f'"Lake County" Illinois (board OR zoning OR development OR "data center")'
    else:
        q = f'"{name}" Illinois (village OR board OR zoning OR development OR plan OR trustees)'
    return GOOGLE.format(q=quote(q))


def village_feed_urls(town: dict) -> list[str]:
    home = town["url"].rstrip("/")
    platform = town.get("platform") or ""
    if platform == "civicplus":
        return [f"{home}/RSSFeed.aspx?ModID=1&CID=All-newsflash.xml"]
    if platform == "wordpress":
        return [f"{home}/feed/"]
    return []


def parse_feed(raw: bytes, feed_name: str, town_name: str) -> list[dict]:
    parsed = feedparser.parse(raw)
    out = []
    for e in parsed.entries:
        title = re.sub(r"\s+", " ", e.get("title", "")).strip()
        link = e.get("link")
        if not title or not link:
            continue
        source = ""
        if feed_name == "google_news":
            src = e.get("source") or {}
            source = src.get("title", "") if isinstance(src, dict) else ""
            # Google appends " - Publisher" to titles
            if source and title.endswith(f" - {source}"):
                title = title[: -len(source) - 3]
            # keep only stories that actually name the town
            if town_name.lower() not in title.lower() and town_name.lower() not in (e.get("summary", "").lower()):
                continue
        published = None
        if e.get("published_parsed"):
            t = e.published_parsed
            published = f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        out.append({"url": link, "title": title, "source": source or town_name,
                    "feed": feed_name, "published": published, "tags": ",".join(tags_for(title)) or None})
    return out
