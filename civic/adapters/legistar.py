"""Legistar public Web API — gives structured agenda items with actions and
vote tallies, so no PDF parsing is needed for the item list."""
from __future__ import annotations

import json
from urllib.parse import quote

from ..util import doc_type
from . import DocLink, Result

API = "https://webapi.legistar.com/v1"


def event_items(payload: list[dict]) -> list[dict]:
    items = []
    for i, it in enumerate(payload):
        title = it.get("EventItemTitle") or it.get("EventItemMatterName") or ""
        if not title.strip():
            continue
        action = (it.get("EventItemActionName") or "").strip() or None
        passed = (it.get("EventItemPassedFlagName") or "").strip()
        items.append({
            "seq": i,
            "number": (it.get("EventItemAgendaNumber") or "").strip() or None,
            "text": title.strip(),
            "action_raw": " ".join(x for x in (action, passed) if x) or None,
            "vote": (it.get("EventItemTally") or "").strip() or None,
            "matter_type": it.get("EventItemMatterType"),
        })
    return items


def collect(town: dict, fetcher, since_iso: str, client: str | None = None, with_items: bool = True, **kw) -> Result:
    res = Result()
    client = client or town.get("legistar")
    if not client:
        res.notes.append("no Legistar client configured")
        return res
    flt = quote(f"EventDate ge datetime'{since_iso}'")
    url = f"{API}/{client}/events?$filter={flt}&$orderby=EventDate desc&$top=1000"
    r = fetcher.get(url)
    if r.status != 200:
        res.notes.append(f"Legistar API {r.status} for client '{client}'")
        return res
    res.pages_crawled += 1
    for ev in json.loads(r.text):
        date = (ev.get("EventDate") or "")[:10] or None
        body = ev.get("EventBodyName")
        items: list[dict] = []
        if with_items:
            ir = fetcher.get(f"{API}/{client}/events/{ev['EventId']}/eventitems?AgendaNote=1&MinutesNote=1")
            if ir.status == 200:
                res.pages_crawled += 1
                items = event_items(json.loads(ir.text))
        page = ev.get("EventInSiteURL") or f"https://{client}.legistar.com/Calendar.aspx"
        for field, kind in (("EventAgendaFile", "agenda"), ("EventMinutesFile", "minutes")):
            if ev.get(field):
                res.add(DocLink(url=ev[field], title=f"{body} {kind} {date}", body=body,
                                doc_type=kind, meeting_date=date, source="legistar", found_on=page,
                                items=items if kind == "agenda" else []))
        if items and not ev.get("EventAgendaFile"):
            # meeting with items but no PDF: keep the items via the meeting page
            res.add(DocLink(url=page, title=f"{body} meeting {date}", body=body, doc_type=doc_type("agenda"),
                            meeting_date=date, source="legistar", found_on=page, items=items))
    return res
