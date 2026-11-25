"""Granicus ViewPublisher pages: one table row per meeting with Agenda /
Minutes / Video links."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..util import body_name, normalize, parse_date
from . import DocLink, Result

VIEW_RE = re.compile(r"https?://[a-z0-9\-]+\.granicus\.com/ViewPublisher\.php\?view_id=\d+", re.I)


def find_publishers(html: str) -> list[str]:
    return sorted(set(VIEW_RE.findall(html)))


def parse_publisher(html: str, base: str) -> list[DocLink]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.find_all("tr"):
        row = tr.get_text(" ", strip=True)
        date = parse_date(row)
        for a in tr.find_all("a", href=True):
            href = normalize(a["href"], base)
            label = a.get_text(" ", strip=True).lower()
            if "agendaviewer" in href.lower() or label == "agenda":
                kind = "agenda"
            elif "minutesviewer" in href.lower() or label == "minutes":
                kind = "minutes"
            elif "metaviewer" in href.lower() or "packet" in label:
                kind = "packet"
            else:
                continue
            out.append(DocLink(url=href, title=row[:150], body=body_name(row), doc_type=kind,
                               meeting_date=date, source="granicus", found_on=base))
    return out


def collect(town: dict, fetcher, since_iso: str, publishers: list[str] | None = None, **kw) -> Result:
    res = Result()
    pubs = list(publishers or town.get("granicus") or [])
    if not pubs:
        r = fetcher.get(town["url"])
        if r.status == 200:
            pubs = find_publishers(r.text)
    if not pubs:
        res.notes.append("no Granicus publisher page found")
        return res
    for p in pubs:
        r = fetcher.get(p)
        if r.status != 200:
            res.notes.append(f"{p} -> {r.status}")
            continue
        res.pages_crawled += 1
        res.docs += [d for d in parse_publisher(r.text, r.url)
                     if not d.meeting_date or d.meeting_date >= since_iso]
    return res
