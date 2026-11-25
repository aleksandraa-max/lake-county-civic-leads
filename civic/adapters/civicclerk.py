"""CivicClerk portals (<tenant>.portal.civicclerk.com) via their public JSON API."""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from ..util import body_name, doc_type
from . import DocLink, Result

TENANT_RE = re.compile(r"https?://([a-z0-9\-]+)\.portal\.civicclerk\.com", re.I)


def find_tenant(html: str) -> str | None:
    m = TENANT_RE.search(html)
    return m.group(1).lower() if m else None


def parse_events(payload: dict, tenant: str) -> list[DocLink]:
    out = []
    api = f"https://{tenant}.api.civicclerk.com/v1"
    for ev in payload.get("value", []):
        date = (ev.get("startDateTime") or ev.get("eventDate") or "")[:10] or None
        name = ev.get("eventName") or ev.get("categoryName") or ""
        body = body_name(name, ev.get("categoryName") or "") or (ev.get("categoryName") or None)
        for f in ev.get("publishedFiles") or []:
            fid = f.get("fileId") or f.get("id")
            if fid is None:
                continue
            ftype = f.get("type") or f.get("name") or ""
            out.append(DocLink(
                url=f"{api}/Meetings/GetMeetingFileStream(fileId={fid},plainText=false)",
                title=f"{name} - {f.get('name') or ftype}".strip(" -"),
                body=body, doc_type=doc_type(ftype, f.get("name") or ""),
                meeting_date=date, source="civicclerk",
                found_on=f"https://{tenant}.portal.civicclerk.com/event/{ev.get('id')}",
            ))
    return out


def collect(town: dict, fetcher, since_iso: str, tenant: str | None = None, **kw) -> Result:
    res = Result()
    tenant = tenant or town.get("civicclerk")
    if not tenant:
        r = fetcher.get(town["url"])
        tenant = find_tenant(r.text) if r.status == 200 else None
    if not tenant:
        res.notes.append("no CivicClerk tenant found")
        return res
    flt = quote(f"startDateTime ge {since_iso}T00:00:00Z")
    url = f"https://{tenant}.api.civicclerk.com/v1/Events?$filter={flt}&$orderby=startDateTime desc&$top=500"
    while url and res.pages_crawled < 20:
        r = fetcher.get(url)
        if r.status != 200:
            res.notes.append(f"CivicClerk API {r.status}")
            break
        res.pages_crawled += 1
        payload = json.loads(r.text)
        res.docs += parse_events(payload, tenant)
        url = payload.get("@odata.nextLink")
    return res
