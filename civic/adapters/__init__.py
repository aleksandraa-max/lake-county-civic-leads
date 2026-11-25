"""Platform adapters. Each returns DocLinks for one town.

Adapters never raise for ordinary site problems: they record them in `notes`
and return what they found, so one broken village never stops the nightly run.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DocLink:
    url: str
    title: str = ""
    body: str | None = None
    doc_type: str = "other"
    meeting_date: str | None = None
    source: str = ""
    found_on: str = ""
    # Structured agenda items when the platform provides them (Legistar).
    items: list[dict] = field(default_factory=list)


@dataclass
class Result:
    docs: list[DocLink] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pages_crawled: int = 0

    def add(self, d: DocLink) -> None:
        self.docs.append(d)


def dedupe(docs: list[DocLink]) -> list[DocLink]:
    seen: dict[str, DocLink] = {}
    for d in docs:
        prev = seen.get(d.url)
        if prev is None:
            seen[d.url] = d
            continue
        # keep the richer record
        for attr in ("title", "body", "meeting_date"):
            if not getattr(prev, attr) and getattr(d, attr):
                setattr(prev, attr, getattr(d, attr))
        if prev.doc_type == "other" and d.doc_type != "other":
            prev.doc_type = d.doc_type
        if d.items and not prev.items:
            prev.items = d.items
    return list(seen.values())
