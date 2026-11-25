"""Weekly report for general contractors.

Pulls agenda/minutes items from the database for a handful of towns and sorts
them into the four things GCs said they care about:

  1. Teardowns and new homes      (earliest signal of a custom-home job)
  2. Bids and contract awards     (who won what, at what price)
  3. Zoning and development cases (variances, special uses, subdivisions...)
  4. Rule changes for builders    (fees, code, setbacks, moratoriums)

Window: meetings from the last `days` days (what was decided) plus the next
`ahead` days (what is coming up). Output: data/reports/gc-weekly-<date>.md and
.html, and data/reports/latest.html. No network access; rule-based only.
"""
from __future__ import annotations

import html
import re
from datetime import date, timedelta
from pathlib import Path

from . import db

DEFAULT_TOWNS = ["highland-park", "deerfield", "libertyville", "vernon-hills", "lake-zurich"]

SECTIONS = [
    ("teardown", "Teardowns and new homes"),
    ("bids", "Bids and contract awards"),
    ("zoning", "Zoning and development cases"),
    ("rules", "Rule changes for builders"),
]

TEARDOWN_RE = re.compile(
    r"tear\s*-?\s*down|demoli|new (single[- ]family )?(home|house|residence)|"
    r"single[- ]family (home|house|residence|dwelling)s?\b.*\b(construct|build|new)|"
    r"(construct|build)\w*\s+(a\s+)?(new\s+)?(single[- ]family|house|home|residence)",
    re.I,
)
# An actual bid result or solicitation, not contract fine print that merely says "contract".
BIDS_RE = re.compile(
    r"bid opening|lowest\s+responsi(ble|ve)\s+(and\s+responsi(ble|ve)\s+)?bid|"
    r"award(ing|ed)?\s+(of\s+)?(a\s+|the\s+)?contract|recommend\w*\s+(to\s+)?award|"
    r"bids?\s+(were\s+)?(received|opened)|(yielded|resulted in|received)\s+\w+\s+(\(\d+\)\s+)?(bids|proposals)|"
    r"request\s+for\s+(proposals|bids|qualifications)|invitation\s+to\s+bid",
    re.I,
)
# Text that is part of a packet but never a lead: contract boilerplate, plan-sheet labels, survey tables.
BOILERPLATE_RE = re.compile(
    r"davis-bacon|prevailing wage act|indemnif|hold harmless|shall (obtain|comply|furnish|maintain|provide)|"
    r"\bsheet [A-Z]-?\d|landscape architects|STREETNO|ROLLNO|PHOTONO|(?-i:\bDATE:\s*[A-Z]{3}\s+\d)|"
    r"(?-i:\bREV\b.{0,20}\bDATE\b)|scale:\s*1\"?\s*=|north arrow|weight analysis",
    re.I,
)
# How a real agenda item starts (used for items cut out of long packets)
AGENDA_START_RE = re.compile(
    r"^\W*(?:[A-Z0-9]{1,4}[.)]\s*)?(?:public hearing|consideration|consider|approval|approve|request|petition|"
    r"an ordinance|ordinance|a resolution|resolution|discussion|presentation|recommendation|award|authoriz|"
    r"case\b|variations?\b|variances?\b|special use|preliminary|final plat|plat\b|zoning (map|text)|text amendment|"
    r"amendment|#?[A-Z]{2,6}-\d{4}-\d{2,6}|\d{1,6}\s+[NSEW]?\.?\s*[A-Z][a-z])",
    re.I,
)
CASE_RE = re.compile(r"#?\b([A-Z]{2,6}-\d{4}-\d{2,6})\b")
RULE_ACT_RE = re.compile(r"\b(ordinance|amend\w*|adopt\w*|text amendment|moratorium)\b", re.I)
PACKET_ITEMS = 60   # a "document" split into more items than this is a meeting packet, not an agenda
RULES_RE = re.compile(
    r"(permit|impact|inspection|plan review|building|tap[- ]on|connection) fees?\b|building codes?\b|"
    r"contractor (registration|licens)|setbacks?\b|tree (removal|preservation)|stormwater (ordinance|regulation)|"
    r"text amendment|moratorium",
    re.I,
)
ZONING_TAGS = {"variance", "special_use", "rezoning", "pud", "subdivision", "site_plan", "annexation",
               "commercial_dev", "industrial", "residential_dev"}
RULE_TAGS = {"text_amendment", "moratorium"}

AMOUNT_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d\d)?|\d{4,}(?:\.\d\d)?)")
VENDOR_RE = re.compile(
    r"\bto\s+([A-Z][A-Za-z0-9&.'\-]*(?:\s+(?:[A-Z][A-Za-z0-9&.'\-]*|of|and|&)){0,6})"
    r"\s*,?\s+(?:in\s+the\s+amount|for\s+an?\s+amount|for\s+\$|not[- ]to[- ]exceed|of\s+[A-Z][a-z]+,?\s+(?:IL|Illinois))"
)
BIDCOUNT_RE = re.compile(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\s+(?:\(\d+\)\s+)?(?:bids|proposals)\b", re.I)

# Applicant names on residential cases are usually private homeowners. Withhold
# "Petitioner John Smith"-style names; keep company names (LLC, Homes, ...).
COMPANY_RE = re.compile(
    r"\b(LLC|L\.L\.C|Inc|Corp|Corporation|Company|Co|Homes|Builders|Construction|Development|Developers|"
    r"Group|Properties|Partners|Trust|Bank|Church|District|Village|City|County|School|Park|Holdings|Investments|"
    r"Realty|Management|Capital|Ventures|Enterprises|Associates|Estate|USA|Signs|Studio|Academy|Center|Restaurant)\b"
)
_ROLE = (r"[Pp]etitioners?(?:\s+and\s+owners?)?|[Aa]pplicants?|[Aa]ppellants?|[Pp]roperty\s+[Oo]wners?|"
         r"[Hh]omeowners?|[Oo]wners?")
_CAP = r"[A-Z][a-z'\-]+"
_ONE = rf"(?:(?:Mr|Mrs|Ms|Dr)\.\s+)?{_CAP}(?:\s+[A-Z]\.)?(?:\s+{_CAP})?"      # Mary T. Sample / Alisa
_TRUSTEE = r"(?:\s*,?\s+as\s+[Tt]rustees?)?"
PERSON_RE = re.compile(
    rf"\b({_ROLE})(\s*[:,]?\s+)({_ONE}{_TRUSTEE}(?:\s*,?\s+(?:and|&)\s+{_ONE}{_TRUSTEE})?)"
)
# "A petition filed by Jane Doe and John Roe, owners" (the role comes after the names)
FILED_BY_RE = re.compile(
    rf"\b((?:filed|submitted|requested)\s+by\s+)({_ONE}(?:\s*,?\s+(?:and|&)\s+{_ONE})?)"
    rf"(?=\s*,\s*(?:the\s+)?(?:property\s+)?(?:owners?|homeowners?|residents?)\b)"
)
# "Address: 2271 Sample Ridge Northbrook, IL 60062" next to an appellant is their home address
HOME_ADDR_RE = re.compile(r"\bAddress:\s*[^:]{3,80}?,?\s+IL\.?\s+\d{5}")


def _looks_personal(name: str, after: str = "") -> bool:
    if COMPANY_RE.search(name) or COMPANY_RE.match(after.strip()):       # "Applicant Pulte Homes", "... Acme LLC"
        return False
    return len(re.findall(r"\b[A-Z][a-z]", name)) >= 2                     # one capitalized word: not clearly a name


def redact(text: str) -> str:
    def sub(m: re.Match) -> str:
        if not _looks_personal(m.group(3), text[m.end():m.end() + 40]):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}(name withheld)"

    def sub_filed(m: re.Match) -> str:
        return f"{m.group(1)}(name withheld)" if _looks_personal(m.group(2)) else m.group(0)

    text = FILED_BY_RE.sub(sub_filed, PERSON_RE.sub(sub, text))
    return HOME_ADDR_RE.sub("Address: (withheld)", text)


def summarize(text: str, limit: int = 280) -> str:
    s = re.sub(r"\s+", " ", text).strip()
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") + "…"


def section_for(text: str, tags: set[str]) -> str | None:
    if "demolition" in tags or TEARDOWN_RE.search(text):
        return "teardown"
    if BIDS_RE.search(text):
        return "bids"
    if tags & ZONING_TAGS:
        return "zoning"
    if tags & RULE_TAGS or (RULES_RE.search(text) and RULE_ACT_RE.search(text)):
        return "rules"
    return None


def readable(text: str) -> bool:
    """False for OCR garbage, plan-sheet labels and boilerplate."""
    tokens = text.split()
    if len(tokens) < 8:
        return False
    words = [t for t in tokens if re.fullmatch(r"[\"'(]?[A-Za-z][A-Za-z'\-]{2,}[.,;:)\"']*", t)]
    if len(words) / len(tokens) < 0.6:
        return False
    odd = re.findall(r"[^\x20-\x7E‘’“”–—•§½… À-ſ]", text)
    if len(odd) > 2:
        return False
    return not BOILERPLATE_RE.search(text)


def strip_page_marks(text: str) -> str:
    return re.sub(r"\bPage \d+ of \d+\b", " ", text)


def bid_details(text: str) -> dict:
    out = {}
    m = AMOUNT_RE.search(text)
    if m:
        out["amount"] = "$" + m.group(1)
    v = VENDOR_RE.search(text)
    if v:
        out["vendor"] = v.group(1).strip(" ,.")
    c = BIDCOUNT_RE.search(text)
    if c:
        out["bids"] = c.group(1).lower()
    return out


def norm_addr(addr: str) -> str:
    a = addr.lower()
    for long, short in (("avenue", "ave"), ("road", "rd"), ("street", "st"), ("drive", "dr"), ("lane", "ln"),
                        ("court", "ct"), ("boulevard", "blvd"), ("parkway", "pkwy")):
        a = re.sub(rf"\b{long}\b", short, a)
    return re.sub(r"[^a-z0-9 ]", "", a).strip()


def group_key(town: str, text: str, addresses: str | None, common: set[str]) -> str:
    """One entry per case: by case number, else by its address, else by the item's opening words.
    Addresses in `common` (village hall letterhead and the like) are never used to group."""
    m = CASE_RE.search(text)
    if m:
        return f"{town}|case|{m.group(1)}"
    for addr in (addresses or "").split(";"):
        a = norm_addr(addr)
        if a and f"{town}|{a}" not in common:
            return f"{town}|addr|{a}"
    return f"{town}|text|{re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()[:90]}"


def _score(e: dict) -> tuple:
    # prefer: the decision (minutes) > a clean agenda line > anything from a short document > shorter text
    return (bool(e["action"]), e["agenda_like"], not e["from_packet"], -len(e["text"]))


def collect_cases(con, towns: list[str], end: date, days: int, ahead: int, classify, order: list[str],
                  needs_place: frozenset = frozenset()) -> list[dict]:
    """Shared by every report: read agenda/minutes items in the window, drop noise, group them into one entry
    per case, and pick the best copy of each. `classify(text, tags, agenda_like)` returns a section name or None;
    `order` ranks sections when a case's pages disagree. Sections in `needs_place` only keep cases that have a
    case number or a property address (that is what separates a case from a staff-report paragraph)."""
    start, until = (end - timedelta(days=days)).isoformat(), (end + timedelta(days=ahead)).isoformat()
    marks = ",".join("?" * len(towns))
    args = (*towns, start, until)
    n_items = dict(con.execute(
        f"""SELECT document_id, count(*) FROM items
            WHERE town IN ({marks}) AND meeting_date >= ? AND meeting_date <= ? GROUP BY document_id""",
        args,
    ).fetchall())
    rows = con.execute(
        f"""SELECT i.document_id, i.town, i.meeting_date, i.body, i.text, i.tags, i.action, i.vote, i.addresses,
                   d.doc_type, d.url, d.title
            FROM items i JOIN documents d ON d.id = i.document_id
            WHERE i.town IN ({marks}) AND i.meeting_date >= ? AND i.meeting_date <= ?
            ORDER BY i.meeting_date DESC""",
        args,
    ).fetchall()

    # addresses printed on many different documents (town hall, police station) say nothing about a case
    docs_per_addr: dict[str, set] = {}
    for r in rows:
        for addr in (r["addresses"] or "").split(";"):
            if addr.strip():
                docs_per_addr.setdefault(f"{r['town']}|{norm_addr(addr)}", set()).add(r["document_id"])
    common = {a for a, docs in docs_per_addr.items() if len(docs) >= 5}

    groups: dict[str, list[dict]] = {}
    for r in rows:
        text = strip_page_marks(r["text"] or "").strip()
        if len(text) < 40 or not readable(text):
            continue
        from_packet = r["doc_type"] == "packet" or n_items.get(r["document_id"], 0) > PACKET_ITEMS
        agenda_like = bool(AGENDA_START_RE.match(text))
        if from_packet and not agenda_like and not r["action"]:
            continue          # a page cut out of a long packet: only keep it if it reads like an agenda item
        tags = set(filter(None, (r["tags"] or "").split(",")))
        sec = classify(text, tags, agenda_like)
        if not sec:
            continue
        entry = {
            "section": sec, "town": r["town"], "date": r["meeting_date"],
            "upcoming": r["meeting_date"] > end.isoformat(), "body": r["body"] or "",
            "action": r["action"], "vote": r["vote"], "addresses": r["addresses"],
            "text": text, "url": r["url"], "doc_type": r["doc_type"],
            "agenda_like": agenda_like, "from_packet": from_packet,
        }
        groups.setdefault(group_key(r["town"], text, r["addresses"], common), []).append(entry)

    cases = []
    for key, members in groups.items():
        # a case that mentions a teardown anywhere is a teardown lead, even if most pages read as "zoning"
        sec = min((m["section"] for m in members), key=order.index)
        if sec in needs_place and "|text|" in key:
            continue
        e = dict(max((m for m in members if m["section"] == sec), key=_score))
        if any(m["action"] for m in members) and not e["action"]:
            decided = max((m for m in members if m["action"]), key=_score)
            e["action"], e["vote"] = decided["action"], decided["vote"]
        e["related"] = len({m["url"] for m in members}) - 1
        e["all_text"] = " ".join(m["text"] for m in members)[:6000]
        # show only the case's own addresses, not the town hall printed in the letterhead
        e["addresses"] = "; ".join(a.strip() for a in (e["addresses"] or "").split(";")
                                   if a.strip() and f"{e['town']}|{norm_addr(a)}" not in common) or None
        e["summary"] = summarize(redact(e["text"]))
        cases.append(e)
    return cases


def _builder_section(text: str, tags: set[str], agenda_like: bool) -> str | None:
    sec = section_for(text, tags)
    if sec == "rules" and not agenda_like:      # ordinance text quoted in a staff report is not a rule change
        return None
    return sec


def gather(con, towns: list[str], end: date, days: int = 7, ahead: int = 21) -> dict[str, list[dict]]:
    order = [s for s, _ in SECTIONS]
    out: dict[str, list[dict]] = {s: [] for s in order}
    for e in collect_cases(con, towns, end, days, ahead, _builder_section, order,
                           needs_place=frozenset({"teardown", "zoning"})):
        if e["section"] == "bids":
            e.update(bid_details(e["text"]))
        out[e["section"]].append(e)
    for s in out:
        out[s].sort(key=lambda e: (e["town"], e["date"] or ""))
    return out


def _status(e: dict) -> str:
    if e["upcoming"]:
        return "Upcoming"
    if e["action"]:
        return e["action"].replace("_", " ").capitalize() + (f" ({e['vote']})" if e["vote"] else "")
    return "On agenda"


def to_markdown(data: dict, names: dict[str, str], end: date, days: int, ahead: int) -> str:
    total = sum(len(v) for v in data.values())
    lines = [f"# Lake County builder report — week ending {end:%B %-d, %Y}", "",
             f"Meetings from {end - timedelta(days=days):%b %-d} to {end:%b %-d}, plus agendas posted for the next "
             f"{ahead} days. Towns: {', '.join(names.get(t, t) for t in names)}. {total} items.", "",
             "_Generated automatically from public agendas and minutes. Check the linked source before acting. "
             "Homeowner names are withheld._", ""]
    for sec, title in SECTIONS:
        items = data[sec]
        lines += [f"## {title} ({len(items)})", ""]
        if not items:
            lines += ["_Nothing this week._", ""]
            continue
        for e in items:
            head = f"**{names.get(e['town'], e['town'])}** · {e['date']} · {e['body'] or 'Meeting'} · {_status(e)}"
            extra = []
            if e.get("addresses"):
                extra.append(e["addresses"])
            for k in ("vendor", "amount"):
                if e.get(k):
                    extra.append(e[k])
            if e.get("bids"):
                extra.append(f"{e['bids']} bids")
            if e.get("related"):
                extra.append(f"{e['related']} related document" + ("s" if e["related"] > 1 else ""))
            lines.append(f"- {head}" + (f" — {' · '.join(extra)}" if extra else ""))
            lines.append(f"  {e['summary']} [source]({e['url']})")
        lines.append("")
    return "\n".join(lines) + "\n"


CSS = """body{font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:860px;margin:32px auto;padding:0 20px;color:#1d2433}
h1{font-size:24px;margin-bottom:4px}h2{font-size:18px;margin-top:32px;border-bottom:2px solid #e3e7ee;padding-bottom:4px}
.meta{color:#5b6475;font-size:13px}.item{padding:10px 0;border-bottom:1px solid #eef1f5}
.head{font-weight:600}.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;background:#eef1f5;margin-left:6px}
.up{background:#fff4d6}.ok{background:#e2f5e6}.no{background:#fde6e6}.facts{color:#334;font-size:13px;margin-top:2px}
.sum{margin-top:4px}a{color:#2456c9}.empty{color:#8a93a3;font-style:italic}
@media print{body{margin:0;max-width:none}a{color:#000}}"""


def to_html(data: dict, names: dict[str, str], end: date, days: int, ahead: int) -> str:
    esc = html.escape
    total = sum(len(v) for v in data.values())
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>Builder report {end.isoformat()}</title>"
             f"<style>{CSS}</style></head><body>",
             f"<h1>Lake County builder report — week ending {esc(f'{end:%B %-d, %Y}')}</h1>",
             f"<p class='meta'>Meetings from {end - timedelta(days=days):%b %-d} to {end:%b %-d}, plus agendas posted for "
             f"the next {ahead} days · {esc(', '.join(names.get(t, t) for t in names))} · {total} items<br>"
             "Generated automatically from public agendas and minutes. Check the linked source before acting. "
             "Homeowner names are withheld.</p>"]
    for sec, title in SECTIONS:
        items = data[sec]
        parts.append(f"<h2>{esc(title)} ({len(items)})</h2>")
        if not items:
            parts.append("<p class='empty'>Nothing this week.</p>")
            continue
        for e in items:
            st = _status(e)
            cls = "up" if e["upcoming"] else ("no" if (e["action"] or "").startswith(("denied", "recommended_denial")) else
                                               ("ok" if e["action"] else ""))
            facts = [x for x in (e.get("addresses"), e.get("vendor"), e.get("amount"),
                                 f"{e['bids']} bids" if e.get("bids") else None,
                                 f"{e['related']} related document" + ("s" if e["related"] > 1 else "")
                                 if e.get("related") else None) if x]
            parts.append(
                "<div class='item'>"
                f"<div class='head'>{esc(names.get(e['town'], e['town']))} · {esc(e['date'] or '')} · "
                f"{esc(e['body'] or 'Meeting')}<span class='tag {cls}'>{esc(st)}</span></div>"
                + (f"<div class='facts'>{esc(' · '.join(facts))}</div>" if facts else "")
                + f"<div class='sum'>{esc(e['summary'])} <a href='{esc(e['url'], quote=True)}'>source</a></div></div>"
            )
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def write_report(con, towns: list[str] | None = None, end: date | None = None, days: int = 7, ahead: int = 21,
                 names: dict[str, str] | None = None, out_dir: Path | None = None, dated: bool = True) -> dict:
    towns = towns or DEFAULT_TOWNS
    end = end or date.today()
    names = {t: (names or {}).get(t, t.replace("-", " ").title()) for t in towns}
    out_dir = out_dir or (db.DATA / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    data = gather(con, towns, end, days, ahead)
    md, page = to_markdown(data, names, end, days, ahead), to_html(data, names, end, days, ahead)
    if dated:  # keep a dated copy; the nightly run only does this on Mondays
        (out_dir / f"gc-weekly-{end.isoformat()}.md").write_text(md)
        (out_dir / f"gc-weekly-{end.isoformat()}.html").write_text(page)
    (out_dir / "latest.md").write_text(md)
    (out_dir / "latest.html").write_text(page)
    return {s: len(v) for s, v in data.items()}
