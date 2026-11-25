"""CivicPlus sites (very common for Illinois villages): Agenda Center by
category and year, plus the generic crawl for Archive Center / Document Center.
"""
from __future__ import annotations

import re
from datetime import date

from ..util import body_name, doc_type, parse_date
from . import DocLink, Result
from .generic import crawl, links_on_page

CAT_RE = re.compile(r"/AgendaCenter/([A-Za-z0-9\-]+)-(\d+)\b")
CAT_ID_RE = re.compile(r"\bcat(\d+)\b")


def categories(html: str, base: str) -> dict[str, str]:
    """catID -> category name, from the Agenda Center landing page."""
    cats: dict[str, str] = {}
    for link, text, _a in links_on_page(html, base):
        m = CAT_RE.search(link)
        if m and m.group(1).lower() not in ("viewfile", "search", "updatecategorylist"):
            cats.setdefault(m.group(2), text or m.group(1).replace("-", " "))
    for cid in CAT_ID_RE.findall(html):
        cats.setdefault(cid, "")
    return cats


def parse_listing(html: str, base: str, cat_name: str, found_on: str) -> list[DocLink]:
    out = []
    for link, text, a in links_on_page(html, base):
        if "/AgendaCenter/ViewFile/" not in link:
            continue
        row = ""
        for p in a.parents:
            if p.name in ("tr", "li"):
                row = p.get_text(" ", strip=True)[:300]
                break
        kind = "minutes" if "/ViewFile/Minutes/" in link else (
            "packet" if "packet" in f"{text} {link}".lower() else "agenda")
        out.append(DocLink(
            url=link, title=text or row[:120],
            body=body_name(cat_name) or body_name(text, row) or (cat_name or None),
            doc_type=kind, meeting_date=parse_date(link, row, text),
            source="civicplus", found_on=found_on,
        ))
    return out


def collect(town: dict, fetcher, since_iso: str, **kw) -> Result:
    home = town["url"].rstrip("/")
    res = Result()
    landing = fetcher.get(home + "/AgendaCenter")
    if landing.status == 200:
        res.pages_crawled += 1
        cats = categories(landing.text, landing.url)
        res.docs += parse_listing(landing.text, landing.url, "", landing.url)
        start_year = int(since_iso[:4])
        for cid, name in cats.items():
            for year in range(start_year, date.today().year + 1):
                u = f"{home}/AgendaCenter/UpdateCategoryList?year={year}&catID={cid}"
                r = fetcher.get(u)
                if r.status == 200:
                    res.pages_crawled += 1
                    res.docs += parse_listing(r.text, home + "/", name, u)
    else:
        res.notes.append(f"AgendaCenter not reachable ({landing.status}); generic crawl only")

    # Archive Center, Document Center, public-hearing pages etc.
    g = crawl(town, fetcher, since_iso, max_pages=kw.get("max_pages", 80),
              extra_seeds=[home + "/Archive.aspx", home + "/DocumentCenter"], source="civicplus+crawl")
    res.docs += g.docs
    res.pages_crawled += g.pages_crawled
    res.docs = [d for d in res.docs if not d.meeting_date or d.meeting_date >= since_iso]
    if not res.docs:
        res.notes += g.notes
    return res


def news_feed_urls(town: dict) -> list[str]:
    home = town["url"].rstrip("/")
    return [f"{home}/RSSFeed.aspx?ModID=1&CID=All-newsflash.xml"]
