"""Generic crawler: works on any municipal site (WordPress, Drupal, Revize,
MunicipalCMS, hand-built). Breadth-first from the homepage and seed pages,
following only meeting/government-looking links, collecting document links.
"""
from __future__ import annotations

from collections import deque
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..util import (
    KEYWORDS_PAGE, SKIP_PAGE, body_name, doc_type, is_doc_link, looks_civic_doc,
    normalize, parse_date, same_site,
)
from . import DocLink, Result

COMMON_PATHS = [
    "/agendas", "/agendas-minutes", "/agendas-and-minutes", "/agenda-minutes", "/meetings",
    "/meeting-agendas", "/minutes", "/board-meetings", "/public-hearings", "/public-notices",
    "/government/agendas-minutes", "/government/meetings", "/AgendaCenter", "/Archive.aspx",
]
# Off-site hosts that serve municipal pages/documents
FRIENDLY_HOSTS = ("revize.com", "granicus.com", "civicclerk.com", "boarddocs.com", "municodemeetings.com",
                  "legistar.com", "amazonaws.com", "civicplus.com", "escribemeetings.com", "laserfiche")


def _friendly(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h in host for h in FRIENDLY_HOSTS)


def _row_context(a) -> str:
    """Text of the table row / list item around a link (often holds the meeting date)."""
    for parent in a.parents:
        if parent.name in ("tr", "li", "p", "article"):
            return parent.get_text(" ", strip=True)[:300]
        if parent.name in ("table", "ul", "body"):
            break
    return ""


def links_on_page(html: str, base_url: str):
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:")):
            continue
        yield normalize(href, base_url), a.get_text(" ", strip=True), a


def crawl(town: dict, fetcher, since_iso: str, max_pages: int = 120, max_depth: int = 3,
          extra_seeds: list[str] | None = None, source: str = "generic") -> Result:
    home = town["url"].rstrip("/")
    res = Result()
    seeds = [home] + list(town.get("seeds") or []) + list(extra_seeds or [])
    seeds += [home + p for p in COMMON_PATHS]
    queue: deque[tuple[str, int]] = deque((s, 0) for s in seeds)
    seen_pages: set[str] = set()
    found: dict[str, DocLink] = {}

    while queue and res.pages_crawled < max_pages:
        url, depth = queue.popleft()
        if url in seen_pages:
            continue
        seen_pages.add(url)
        r = fetcher.get(url)
        if r.status != 200 or "html" not in (r.content_type or "text/html"):
            continue
        res.pages_crawled += 1
        page_body = body_name(url)
        for link, text, a in links_on_page(r.text, r.url):
            if is_doc_link(link):
                ctx = _row_context(a)
                if not looks_civic_doc(link, f"{text} {ctx}"):
                    continue
                if link not in found:
                    found[link] = DocLink(
                        url=link,
                        title=text or ctx[:120],
                        body=body_name(text, ctx, link) or page_body,
                        doc_type=doc_type(text, link, ctx),
                        meeting_date=parse_date(text, link, ctx),
                        source=source,
                        found_on=r.url,
                    )
                continue
            if depth >= max_depth or SKIP_PAGE.search(link):
                continue
            if not (same_site(link, home) or _friendly(link)):
                continue
            if KEYWORDS_PAGE.search(link) or KEYWORDS_PAGE.search(text):
                # year/archive pages are where history lives; keep them
                queue.append((link, depth + 1))

    for d in found.values():
        if d.meeting_date and d.meeting_date < since_iso:
            continue
        res.add(d)
    if not res.pages_crawled:
        res.notes.append("homepage and seed pages unreachable")
    elif not res.docs:
        res.notes.append(f"crawled {res.pages_crawled} pages, no meeting documents found; add a seed URL")
    return res
