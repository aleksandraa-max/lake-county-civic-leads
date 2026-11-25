"""The nightly pipeline: discover -> collect -> extract -> classify -> news -> export.

Every step is incremental and safe to re-run. A run is time-boxed; whatever
isn't fetched tonight stays `pending` and is picked up tomorrow, so the first
multi-year backfill simply spreads over several nights.
"""
from __future__ import annotations

import csv
import json
import time
from datetime import date, timedelta
from pathlib import Path

import yaml

from . import db
from .adapters import Result, dedupe
from .adapters import civicclerk, civicplus, generic, granicus, legistar
from .classify import LAND_USE, analyze_item, normalize_action, split_items, tags_for
from .extract import to_text
from .news import google_news_url, parse_feed, village_feed_urls
from .util import now_iso

CONFIG = db.ROOT / "config" / "towns.yaml"


def load_towns(only: list[str] | None = None, config: Path = CONFIG) -> list[dict]:
    towns = yaml.safe_load(config.read_text())["towns"]
    if only:
        towns = [t for t in towns if t["slug"] in only]
    return towns


def default_since(years: int = 3) -> str:
    return (date.today() - timedelta(days=365 * years)).isoformat()


# ---------------------------------------------------------------- discover
def fingerprint(html: str) -> dict:
    h = html.lower()
    fp = {"platform": "generic"}
    if "civicplus" in h or "/agendacenter" in h or "government websites by" in h:
        fp["platform"] = "civicplus"
    elif "wp-content" in h:
        fp["platform"] = "wordpress"
    elif "revize" in h:
        fp["platform"] = "revize"
    elif "drupal" in h or "/sites/default/files" in h:
        fp["platform"] = "drupal"
    elif "municipalcms" in h or "docview.aspx" in h:
        fp["platform"] = "municipalcms"
    pubs = granicus.find_publishers(html)
    if pubs:
        fp["granicus"] = pubs
    tenant = civicclerk.find_tenant(html)
    if tenant:
        fp["civicclerk"] = tenant
    if "legistar.com" in h:
        import re
        m = re.search(r"https?://([a-z0-9\-]+)\.legistar\.com", h)
        if m:
            fp["legistar"] = m.group(1)
    if "boarddocs.com" in h:
        fp["boarddocs"] = True
    return fp


def discover(con, fetcher, towns: list[dict]) -> list[dict]:
    report = []
    for t in towns:
        r = fetcher.get(t["url"])
        row = {"slug": t["slug"], "name": t["name"], "url": t["url"], "http": r.status,
               "final_url": r.url, "verified_in_config": t.get("verified", False)}
        if r.status == 200:
            fp = fingerprint(r.text)
            row.update(fp)
            status = "ok"
        else:
            row["platform"] = t.get("platform") or "unknown"
            status = "unreachable"
        con.execute(
            """INSERT INTO towns(slug,name,kind,url,platform,status,last_discovered,note)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(slug) DO UPDATE SET url=excluded.url, platform=excluded.platform,
                 status=excluded.status, last_discovered=excluded.last_discovered""",
            (t["slug"], t["name"], t["kind"], r.url if r.status == 200 else t["url"],
             t.get("platform") or row["platform"], status, now_iso(), t.get("note")),
        )
        report.append(row)
    con.commit()
    out = db.DATA / "discovery.json"
    out.write_text(json.dumps(report, indent=2))
    return report


def _detected(con, t: dict) -> dict:
    """Merge config with what discovery found."""
    t = dict(t)
    path = db.DATA / "discovery.json"
    if path.exists():
        for row in json.loads(path.read_text()):
            if row["slug"] == t["slug"]:
                for k in ("granicus", "civicclerk", "legistar"):
                    if row.get(k) and not t.get(k):
                        t[k] = row[k]
                if not t.get("platform") and row.get("platform"):
                    t["platform"] = row["platform"]
                if row.get("http") == 200 and row.get("final_url"):
                    t["url"] = row["final_url"].rstrip("/")
    return t


# ----------------------------------------------------------------- collect
def collect_town(t: dict, fetcher, since: str, max_pages: int = 120) -> Result:
    res = Result()
    parts: list[Result] = []
    if t.get("legistar"):
        parts.append(legistar.collect(t, fetcher, since))
    if t.get("civicclerk"):
        parts.append(civicclerk.collect(t, fetcher, since))
    if t.get("granicus"):
        parts.append(granicus.collect(t, fetcher, since, publishers=t["granicus"]))
    if t.get("platform") == "civicplus":
        parts.append(civicplus.collect(t, fetcher, since, max_pages=max_pages))
    else:
        parts.append(generic.crawl(t, fetcher, since, max_pages=max_pages))
    for p in parts:
        res.docs += p.docs
        res.notes += p.notes
        res.pages_crawled += p.pages_crawled
    res.docs = dedupe(res.docs)
    return res


def store_docs(con, town: str, res: Result) -> int:
    new = 0
    for d in res.docs:
        cur = con.execute(
            """INSERT OR IGNORE INTO documents(town,url,title,body,doc_type,meeting_date,source,found_on,first_seen,fetch_status)
               VALUES(?,?,?,?,?,?,?,?,?, 'pending')""",
            (town, d.url, d.title[:300], d.body, d.doc_type, d.meeting_date, d.source, d.found_on, now_iso()),
        )
        if cur.rowcount:
            new += 1
            doc_id = cur.lastrowid
            for it in d.items:
                a = analyze_item(it["text"], is_minutes=False)
                con.execute(
                    """INSERT INTO items(document_id,town,meeting_date,body,seq,number,text,tags,action,vote,addresses,pins)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (doc_id, town, d.meeting_date, d.body, it["seq"], it.get("number"), it["text"], a["tags"],
                     normalize_action(it.get("action_raw")), it.get("vote"), a["addresses"], a["pins"]),
                )
        else:
            # fill gaps on existing rows
            con.execute(
                """UPDATE documents SET title=COALESCE(NULLIF(title,''),?), body=COALESCE(body,?),
                   meeting_date=COALESCE(meeting_date,?) WHERE url=?""",
                (d.title[:300], d.body, d.meeting_date, d.url),
            )
    con.commit()
    return new


def collect(con, fetcher, towns: list[dict], since: str, deadline: float, max_pages: int = 120) -> dict:
    summary = {}
    for t in towns:
        if time.monotonic() > deadline:
            summary[t["slug"]] = "skipped: out of time"
            continue
        t = _detected(con, t)
        con.execute("INSERT OR IGNORE INTO towns(slug,name,kind,url,platform,note) VALUES(?,?,?,?,?,?)",
                    (t["slug"], t["name"], t["kind"], t["url"], t.get("platform"), t.get("note")))
        res = collect_town(t, fetcher, since, max_pages=max_pages)
        new = store_docs(con, t["slug"], res)
        status = "ok" if res.docs else ("unreachable" if not res.pages_crawled else "no_documents_found")
        con.execute("UPDATE towns SET last_collected=?, status=?, note=COALESCE(?, note) WHERE slug=?",
                    (now_iso(), status, "; ".join(res.notes) or None, t["slug"]))
        con.commit()
        summary[t["slug"]] = {"found": len(res.docs), "new": new, "pages": res.pages_crawled, "notes": res.notes}
        print(f"[collect] {t['slug']}: {len(res.docs)} docs ({new} new), {res.pages_crawled} pages {res.notes or ''}",
              flush=True)
    return summary


# ----------------------------------------------------------------- extract
def extract(con, fetcher, deadline: float, limit: int = 5000, recheck_days: int = 21) -> dict:
    """Fetch pending docs (newest meetings first). Agendas from the last few weeks
    are re-fetched because villages replace them with final packets."""
    recent = (date.today() - timedelta(days=recheck_days)).isoformat()
    rows = con.execute(
        """SELECT id,town,url,doc_type,sha256 FROM documents
           WHERE fetch_status='pending'
              OR (fetch_status='ok' AND doc_type IN ('agenda','packet') AND meeting_date >= ?
                  AND (fetched_at IS NULL OR fetched_at < date('now','-2 days')))
           ORDER BY meeting_date IS NULL, meeting_date DESC LIMIT ?""",
        (recent, limit),
    ).fetchall()
    stats = {"ok": 0, "error": 0, "no_text": 0, "changed": 0}
    for row in rows:
        if time.monotonic() > deadline:
            break
        r = fetcher.get(row["url"])
        if r.status != 200:
            con.execute("UPDATE documents SET fetch_status=?, fetched_at=? WHERE id=?",
                        (f"error:http {r.status}", now_iso(), row["id"]))
            stats["error"] += 1
            continue
        try:
            text, kind = to_text(r.content, r.content_type, r.url)
        except Exception as e:  # noqa: BLE001
            con.execute("UPDATE documents SET fetch_status=?, fetched_at=? WHERE id=?",
                        (f"error:parse {type(e).__name__}", now_iso(), row["id"]))
            stats["error"] += 1
            continue
        sha, rel = db.save_text(row["town"], r.content, text)
        status = "ok" if len(text.strip()) >= 200 else "ok:no_text"
        if status == "ok:no_text":
            stats["no_text"] += 1
        if row["sha256"] and row["sha256"] != sha:
            stats["changed"] += 1
            con.execute("DELETE FROM items WHERE document_id=?", (row["id"],))
        con.execute(
            """UPDATE documents SET fetch_status=?, fetched_at=?, content_type=?, sha256=?, n_chars=?, text_path=?
               WHERE id=?""",
            (status, now_iso(), kind, sha, len(text), rel, row["id"]),
        )
        stats["ok"] += 1
        if stats["ok"] % 25 == 0:
            con.commit()
            print(f"[extract] {stats}", flush=True)
    con.commit()
    return stats


# ---------------------------------------------------------------- classify
def classify(con) -> int:
    rows = con.execute(
        """SELECT d.* FROM documents d
           WHERE d.fetch_status='ok' AND d.text_path IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM items i WHERE i.document_id=d.id)"""
    ).fetchall()
    n = 0
    for d in rows:
        text = db.load_text(d["text_path"])
        is_minutes = d["doc_type"] == "minutes"
        items = split_items(text)
        if not items:  # unstructured doc (notice, letter): keep it as one item
            items = [{"seq": 0, "number": None, "text": text[:2500]}]
        for it in items:
            a = analyze_item(it["text"], is_minutes)
            con.execute(
                """INSERT INTO items(document_id,town,meeting_date,body,seq,number,text,tags,action,vote,addresses,pins)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d["id"], d["town"], d["meeting_date"], d["body"], it["seq"], it["number"], it["text"],
                 a["tags"], a["action"], a["vote"], a["addresses"], a["pins"]),
            )
            n += 1
    con.commit()
    return n


# -------------------------------------------------------------------- news
def news(con, fetcher, towns: list[dict]) -> int:
    n = 0
    for t in towns:
        t = _detected(con, t)
        feeds = [("google_news", google_news_url(t))] + [("village_rss", u) for u in village_feed_urls(t)]
        for name, url in feeds:
            r = fetcher.get(url)
            if r.status != 200:
                continue
            for e in parse_feed(r.content, name, t["name"]):
                cur = con.execute(
                    "INSERT OR IGNORE INTO news(town,url,title,source,feed,published,first_seen,tags) VALUES(?,?,?,?,?,?,?,?)",
                    (t["slug"], e["url"], e["title"], e["source"], e["feed"], e["published"], now_iso(), e["tags"]),
                )
                n += cur.rowcount
    con.commit()
    return n


# ------------------------------------------------------------------ export
def export(con, out_dir: Path | None = None, digest_days: int = 180) -> None:
    out_dir = out_dir or db.DATA
    (out_dir / "exports").mkdir(parents=True, exist_ok=True)
    (out_dir / "digests").mkdir(parents=True, exist_ok=True)

    land_use_sql = " OR ".join(f"i.tags LIKE '%{t}%'" for t in sorted(LAND_USE))
    rows = con.execute(
        f"""SELECT i.town, i.meeting_date, i.body, d.doc_type, i.number, i.action, i.vote, i.tags,
                   i.addresses, i.pins, substr(i.text,1,600) AS text, d.url
            FROM items i JOIN documents d ON d.id=i.document_id
            WHERE {land_use_sql}
            ORDER BY i.meeting_date DESC, i.town"""
    ).fetchall()
    with open(out_dir / "exports" / "land_use_items.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(rows[0].keys() if rows else ["town", "meeting_date", "body", "doc_type", "number", "action",
                                                 "vote", "tags", "addresses", "pins", "text", "url"])
        for r in rows:
            w.writerow(list(r))

    since = (date.today() - timedelta(days=digest_days)).isoformat()
    towns = con.execute("SELECT * FROM towns ORDER BY name").fetchall()
    status_lines = ["# Collection status", "", f"Updated {now_iso()}", "",
                    "| Town | Platform | Status | Documents | Items | Land-use items | Latest meeting | Note |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for t in towns:
        c = con.execute(
            f"""SELECT (SELECT count(*) FROM documents WHERE town=?) docs,
                       (SELECT count(*) FROM items WHERE town=?) items,
                       (SELECT count(*) FROM items i WHERE town=? AND ({land_use_sql})) lu,
                       (SELECT max(meeting_date) FROM documents WHERE town=? AND meeting_date<=date('now','+60 days')) latest""",
            (t["slug"],) * 4,
        ).fetchone()
        note = (t["note"] or "").replace("|", "/")[:120]
        status_lines.append(f"| {t['name']} | {t['platform'] or ''} | {t['status'] or ''} | {c['docs']} | {c['items']} | "
                            f"{c['lu']} | {c['latest'] or ''} | {note} |")

        # per-town digest
        items = con.execute(
            f"""SELECT i.meeting_date, i.body, d.doc_type, i.action, i.vote, i.tags, i.addresses,
                       substr(i.text,1,400) AS text, d.url
                FROM items i JOIN documents d ON d.id=i.document_id
                WHERE i.town=? AND i.meeting_date>=? AND ({land_use_sql})
                ORDER BY i.meeting_date DESC LIMIT 200""",
            (t["slug"], since),
        ).fetchall()
        news_rows = con.execute(
            "SELECT published,title,source,url FROM news WHERE town=? AND (published>=? OR published IS NULL) "
            "ORDER BY published DESC LIMIT 50", (t["slug"], since)).fetchall()
        lines = [f"# {t['name']} — land use and development, last {digest_days} days", "",
                 f"Updated {now_iso()}. Source documents are linked; this file is generated.", "",
                 "## Agenda and minutes items", ""]
        if not items:
            lines.append("_No land-use items found in this window._")
        for it in items:
            flag = f" **{it['action']}**" if it["action"] else ""
            vote = f" ({it['vote']})" if it["vote"] else ""
            addr = f" — {it['addresses']}" if it["addresses"] else ""
            lines.append(f"- {it['meeting_date'] or 'undated'} · {it['body'] or ''} {it['doc_type']}{flag}{vote}{addr}  ")
            lines.append(f"  `{it['tags']}` — {it['text'].strip()} [source]({it['url']})")
        lines += ["", "## News", ""]
        if not news_rows:
            lines.append("_No news found in this window._")
        for nrow in news_rows:
            lines.append(f"- {nrow['published'] or ''} · [{nrow['title']}]({nrow['url']}) — {nrow['source']}")
        (out_dir / "digests" / f"{t['slug']}.md").write_text("\n".join(lines) + "\n")

    (out_dir / "STATUS.md").write_text("\n".join(status_lines) + "\n")
