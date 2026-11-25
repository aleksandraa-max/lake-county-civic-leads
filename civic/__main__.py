"""Command line.

  python -m civic run                 # everything, time-boxed (what the nightly job runs)
  python -m civic discover            # check every town's website and detect its platform
  python -m civic collect --towns long-grove,kildeer
  python -m civic extract --max-minutes 30
  python -m civic classify
  python -m civic news
  python -m civic export
  python -m civic search "quiktrip"   # full-text search across collected items
  python -m civic report              # weekly GC report + commercial build-out leads -> data/reports/
  python -m civic report --towns deerfield,highland-park --end 2026-11-23
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date

from . import commercial, db, pipeline, report
from .http import Fetcher
from .util import now_iso


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="civic")
    p.add_argument("command", choices=["run", "discover", "collect", "extract", "classify", "news", "export", "search",
                                       "stats", "report"])
    p.add_argument("query", nargs="?", help="search text (for `search`)")
    p.add_argument("--towns", help="comma-separated slugs (default: all)")
    p.add_argument("--since", help="earliest meeting date to keep, YYYY-MM-DD (default: 3 years ago)")
    p.add_argument("--max-minutes", type=float, default=330, help="time box for the whole run")
    p.add_argument("--max-pages", type=int, default=120, help="pages crawled per town")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between requests to the same host")
    p.add_argument("--end", help="report: last day of the week, YYYY-MM-DD (default: today)")
    p.add_argument("--days", type=int, default=7, help="report: days of past meetings to include")
    a = p.parse_args(argv)

    con = db.connect()
    towns = pipeline.load_towns(a.towns.split(",") if a.towns else None)
    since = a.since or pipeline.default_since()
    start = time.monotonic()
    deadline = start + a.max_minutes * 60
    fetcher = Fetcher(delay=a.delay)
    started = now_iso()
    summary: dict = {}

    if a.command in ("run", "discover"):
        rep = pipeline.discover(con, fetcher, towns)
        bad = [r["slug"] for r in rep if r["http"] != 200]
        summary["discover"] = {"towns": len(rep), "unreachable": bad}
        print(f"[discover] {len(rep)} towns, unreachable: {bad}", flush=True)
    if a.command in ("run", "collect"):
        # leave ~45% of the time box for downloading documents
        collect_deadline = start + a.max_minutes * 60 * (0.5 if a.command == "run" else 1.0)
        summary["collect"] = pipeline.collect(con, fetcher, towns, since, collect_deadline, max_pages=a.max_pages)
    if a.command in ("run", "news"):
        summary["news_new"] = pipeline.news(con, fetcher, towns)
        print(f"[news] {summary['news_new']} new stories", flush=True)
    if a.command in ("run", "extract"):
        summary["extract"] = pipeline.extract(con, fetcher, deadline - 300 if a.command == "run" else deadline)
        print(f"[extract] {summary['extract']}", flush=True)
    if a.command in ("run", "classify"):
        summary["items_added"] = pipeline.classify(con)
        print(f"[classify] {summary['items_added']} items", flush=True)
    if a.command in ("run", "export"):
        pipeline.export(con)
        print("[export] wrote data/STATUS.md, data/digests/, data/exports/", flush=True)
    if a.command in ("run", "report"):
        # `run --towns x` limits collection, not the report: the report always covers its own town list
        slugs = a.towns.split(",") if (a.command == "report" and a.towns) else report.DEFAULT_TOWNS
        names = {t["slug"]: t["name"] for t in pipeline.load_towns(slugs)}
        end = date.fromisoformat(a.end) if a.end else date.today()
        dated = a.command == "report" or end.weekday() == 0   # nightly: a dated copy each Monday, latest.* every night
        summary["report"] = report.write_report(con, slugs, end=end, days=a.days, names=names, dated=dated)
        print(f"[report] data/reports/latest.html {summary['report']}", flush=True)
        # commercial build-out leads cover every town (or just --towns for `report --towns ...`)
        every = pipeline.load_towns(a.towns.split(",") if (a.command == "report" and a.towns) else None)
        summary["commercial"] = commercial.write_report(con, [t["slug"] for t in every], end=end,
                                                        names={t["slug"]: t["name"] for t in every}, dated=dated)
        print(f"[report] data/reports/commercial-latest.html {summary['commercial']}", flush=True)
    if a.command == "search":
        q = f"%{(a.query or '').lower()}%"
        for r in con.execute(
            """SELECT i.town,i.meeting_date,i.body,i.action,substr(i.text,1,200) t,d.url FROM items i
               JOIN documents d ON d.id=i.document_id WHERE lower(i.text) LIKE ? ORDER BY i.meeting_date DESC LIMIT 50""",
            (q,),
        ):
            print(f"{r['meeting_date']} {r['town']:<18} {r['body'] or '':<24} {r['action'] or '':<10} {r['t']}\n    {r['url']}")
        return
    if a.command == "stats":
        for row in con.execute("SELECT fetch_status, count(*) n FROM documents GROUP BY 1 ORDER BY 2 DESC"):
            print(f"{row['fetch_status']:<30} {row['n']}")
        print("items:", con.execute("SELECT count(*) FROM items").fetchone()[0])
        print("news:", con.execute("SELECT count(*) FROM news").fetchone()[0])
        return

    con.execute("INSERT INTO runs(started,finished,command,summary) VALUES(?,?,?,?)",
                (started, now_iso(), a.command, json.dumps(summary, default=str)[:20000]))
    con.commit()
    print(f"done in {(time.monotonic() - start) / 60:.1f} min; {fetcher.requests_made} requests", flush=True)


if __name__ == "__main__":
    main()
