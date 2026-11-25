"""Commercial build-out leads: private commercial projects a GC can win (target: jobs under about $2M).

Same idea as the hand-run lead-monitor digest, but over every town the nightly job collects:
special uses, site plans, liquor licenses, restaurants, medical, daycare, fitness, retail and
industrial build-outs, ranked

  A  act this week        approved (or new liquor license / new owner) in an existing building
  B  build the relationship  pending cases, multi-tenant centers leasing up, spec industrial
  C  watch                ground-up and chain projects that are probably over $2M

Output: data/reports/commercial-latest.md, .html and .tsv. The TSV has no header and uses the
column order of a lead-tracker spreadsheet (Category, Project/Business, Town, Address, Signal & date,
Likely scope, Size fit, Entry path, Priority, Source URL), so new leads drop into the Live Leads tab.
"""
from __future__ import annotations

import html
import re
from datetime import date, timedelta
from pathlib import Path

from . import db
from .report import CSS, _looks_personal, _status, collect_cases, norm_addr, redact

PRIORITIES = [("A", "Act this week"), ("B", "Build the relationship"), ("C", "Watch")]

COMMERCIAL_TAGS = {"special_use", "site_plan", "commercial_dev", "liquor_gaming", "industrial", "cannabis",
                   "car_wash", "gas_station", "pud"}
CATEGORIES = [
    ("Restaurant", r"\b(liquor licen[cs]es?|restaurant|bar|brewery|taproom|tavern|caf[eé]|coffee|bakery|pizza|grill)\b"),
    ("Fitness", r"\b(fitness|gym|gymnastics|cross ?fit|athletic|training facility|martial arts|yoga|dance (studio|academy)|"
                r"fencing club|swim school|golf(?! course)|self-improvement)\b"),
    ("Medical", r"\b(medical|dental|dentist|clinic|physician|urgent care|veterinar\w*|animal hospital|physical therapy|"
                r"chiropract\w*)"),
    ("Daycare", r"\b(day ?care|child ?care|preschool|early learning|montessori)"),
    ("Bank", r"\b(bank|credit union)\b"),
    ("Industrial", r"\b(industrial|warehouse|distribution cent\w*|manufactur\w*|truck repair|flex space)"),
    ("Cannabis", r"\b(cannabis|dispensar\w*)"),
    ("Auto", r"\b(car wash|gas station|fuel station|motor fuel|auto(motive)? (repair|service|sales|body)|body shop|"
             r"(motor )?vehicle (sales|repair))"),
    ("Retail", r"\b(retail|stores?|storefront|shop|shopping|salon|spa|boutique|showroom|tenants?|equipment rental|home improvement)\b"),
]
_CATEGORIES = [(name, re.compile(rx, re.I)) for name, rx in CATEGORIES]
COMMERCIAL_RE = re.compile("|".join(rx for _, rx in CATEGORIES) + r"|build-?out|commercial (tenant|space|building|center|unit)|office (space|building|suite|use)", re.I)
# words that make a case commercial even when it also mentions yards, garages or a residential district
STRONG_COMMERCIAL_RE = re.compile(r"\b(liquor|restaurant|retail|tenants?|commercial|stores?|office (space|suite|building|use)|industrial|"
                                  r"warehouse|medical|clinic|bank|dispensar\w*|car wash|gas station|day ?care|child ?care|"
                                  r"preschool|fitness|studio|salon)\b", re.I)
PUBLIC_RE = re.compile(r"village hall|city hall|public works|water (main|tower|treatment|system)|park district|"
                       r"school district|police|fire (station|department|protection district)|library|resurfac|"
                       r"sewer|street light|sidewalk|bike path|budget|tax levy|audit", re.I)
RESIDENTIAL_RE = re.compile(r"single[- ]family|townhom|residen|dwelling|\bfences?\b|\bgarages?\b|\bdecks?\b|\bpatios?\b|"
                            r"swimming pool|accessory structure|home occupation|(front|rear|side) yard|\bsubdivision\b", re.I)
GAMING_ONLY_RE = re.compile(r"video gaming|gaming terminal", re.I)

GROUND_UP_RE = re.compile(r"construct(ion of)?\s+(a\s+)?new\s+(\d[\d,]*\s*(sq|square)[^ ]*\s+)?(building|facility|structure)|"
                          r"new\s+\d[\d,]*\s*(sq\.?\s*ft|square[- ]f(oo|ee)t)\s+(building|facility)|ground[- ]up|"
                          r"drive[- ]?(thru|through)|outlot|car wash|gas station|fuel station|assisted living|hotel|"
                          r"apartments?\b|redevelop", re.I)
INTERIOR_RE = re.compile(r"existing (\w+[- ])?(building|facility|tenant space|space|unit|suite|storefront|multi-tenant)|"
                         r"tenant space|\bsuite\s+\w+|\bunit\s+\w+|build-?out|interior", re.I)
EXTERIOR_RE = re.compile(r"fa[cç]ade|exterior|\bsigns?\b|signage|parking lot|elevation", re.I)
NEW_OPERATOR_RE = re.compile(r"liquor licen[cs]e|new owner|change (of|in) owner|under new ownership|contract purchaser", re.I)
BIG_SF_RE = re.compile(r"(\d{2,3}),(\d{3})\s*(sq|square)", re.I)

WHO_RE = re.compile(
    r"\b(?:filed|submitted|requested)\s+by\s+([A-Z][\w'&.\-]*(?:\s+(?:[A-Z&][\w'&.\-]*|of|and)){0,6})"
    r"|\bd/?b/?a\.?\s+([A-Z][\w'&.\-]*(?:\s+[A-Z&][\w'&.\-]*){0,5})"
    r"|\bdoing business as\s+([A-Z][\w'&.\-]*(?:\s+[A-Z&][\w'&.\-]*){0,5})"
)
_WHAT_END = r"(?=\s+(?:at|in|on|for|located|within|per|pursuant|to be|and approval)\b|[,.;(–—])"
_WHAT = r"([a-z][a-z0-9\x27&/\- ]{3,50}?)"
# "to permit a truck repair shop at ..." first; then "for a ... at" unless it is the permit itself ("for a Special Use")
WHAT_RES = [
    re.compile(r"\bto\s+(?:permit|allow|operate|establish|open)\s+(?:a|an)\s+" + _WHAT + _WHAT_END, re.I),
    re.compile(r"\bestablishment of\s+(?:an?\s+)?" + _WHAT + _WHAT_END, re.I),               # "... of a Self-Improvement Facility"
    re.compile(r"\bto facilitate the (?:construction|development|operation) of\s+(?:an?\s+)?" + _WHAT + _WHAT_END, re.I),
    re.compile(r"\b(?:special|conditional) use(?: permit)?\s+(?:for|to allow|to permit)\s+(?:an?\s+)?" + _WHAT + _WHAT_END, re.I),
    re.compile(r"\bfor\s+(?:a|an)\s+(?!special|permit|variation|variance|text|zoning|planned|public|conditional|amendment)"
               + _WHAT + _WHAT_END, re.I),
]
# when no business or use is named, say what kind of case it is ("Planned development amendment and sign package")
KINDS = [("planned development", r"planned (unit )?development|\bPUD\b"), ("special use", r"special use"),
         ("conditional use", r"conditional use"), ("sign package", r"sign package|sign plan"),
         ("design review", r"design review"), ("site plan", r"site plan"), ("variation", r"variation|variance"),
         ("rezoning", r"rezoning|map amendment"), ("subdivision", r"subdivision")]
# a house case (garages, fences, pools) is not a commercial lead, whatever else its packet mentions
HOUSE_RE = re.compile(r"\bgarages?\b|\bfences?\b|\bdecks?\b|\bpatios?\b|swimming pool|accessory structure|"
                      r"home occupation|single[- ]family", re.I)


LEGAL_PREFIX_RE = re.compile(
    r"^(?:[\W\d]+|(?:approval|passage|and|of|consideration|public|hearing|meeting|on|the|an?|request|for|ordinance|"
    r"resolution|no\.?|number|amending|amend|title|chapter|section|village|city|code|[A-Z0-9]+-[A-Z0-9-]+)\b[\s:.,\-–—]*)+",
    re.I)


def classify(text: str, tags: set[str], agenda_like: bool) -> str | None:
    """'lead' for a private commercial project, else None (residential, public works, gaming-only)."""
    commercial = bool(tags & COMMERCIAL_TAGS) or bool(COMMERCIAL_RE.search(text))
    if not commercial:
        return None
    # a house, fence or garage case stays out unless the text names a business use
    if RESIDENTIAL_RE.search(text) and not STRONG_COMMERCIAL_RE.search(text) and not tags & {"commercial_dev", "liquor_gaming"}:
        return None
    if PUBLIC_RE.search(text) and not WHO_RE.search(text):
        return None
    if ADMIN_RE.search(text):
        return None
    if GAMING_ONLY_RE.search(text) and not re.search(r"liquor|restaurant", text, re.I):
        return None
    return "lead"


def category(text: str) -> str:
    for name, rx in _CATEGORIES:
        if rx.search(text):
            return name
    return "Commercial"


_NAME = r"([A-Z][\w'’&.\-]*(?:,?\s+(?:[A-Z&][\w'’&.\-]*|LLC|L\.L\.C\.|Inc\.?|of|and|the)){0,6})"
# Where agendas put the business name, most specific first
NAME_RES = [
    re.compile(r"\b(?i:licen[cs]es?\s+(?:to|for))\s+" + _NAME),                       # "Liquor License to Akshar19, LLC"
    re.compile(r"\bd/?b/?a\.?\s+" + _NAME),
    re.compile(r"\bdoing business as\s+" + _NAME, re.I),
    # "... by One – Mr. Gyo's Asian Street Food – 2234 E. Grand Avenue", "Unit C - Back Nine Golf and CRM Properties –"
    re.compile(r"\s[–—-]\s+((?:Mr\.|Mrs\.|Ms\.)?\s*[A-Z][^–—]{2,60}?)\s+[–—-]\s"),
    re.compile(r"\b(?i:for an?)\s+[\w\s-]{3,40}?,\s+([A-Z][\w'’&.\- ]{2,40}?),\s+(?i:to be located|located)"),
    re.compile(r"\b(?i:owner and operator|operator) of\s+" + _NAME),
    re.compile(r"^((?:[A-Z][\w'’&.\-]*\s+){0,4}[A-Z][\w'’&.\-]*):\s+\d"),            # "Deerfield Commons: 710 Deerfield Road ..."
    re.compile(r"\b(?i:for|of) the\s+((?:[A-Z][\w'’&.\-]*\s+){1,6}?(?:Planned (?:Unit )?Development|Commerce Center|"
               r"Shopping Center|Business (?:Park|Center)|Plaza|Commons|Mall))\b"),
]


_SHORT = set("a i am an as at be by do go he if in is it me my no of on or so to up us we id ox "
             "st rd dr ln ct ft sq pm th nd mr ms il ne nw se sw ii iii iv vi vii viii ix xi pd".split())


def ocr_fix(s: str) -> str:
    """Rejoin words that PDF extraction split apart: 'recycl e facility', 'co nditional', 'EL CAR WA SH', 'drive -through'."""
    if not s:
        return s
    s = re.sub(r"\b([a-z]{3,}) ([a-z]{1,2})(?=[\s,.;:)]|$)",
               lambda m: m.group(0) if m.group(2) in _SHORT else m.group(1) + m.group(2), s)
    s = re.sub(r"(?<![\w-])([a-z]{1,2}) ([a-z]{3,})\b",
               lambda m: m.group(0) if m.group(1) in _SHORT else m.group(1) + m.group(2), s)
    s = re.sub(r"\b([A-Z]{2,}) ([A-Z]{2})(?=[\s,.;:)]|$)(?! [A-Z]{1,2}\b)",
               lambda m: m.group(0) if m.group(2).lower() in _SHORT else m.group(1) + m.group(2), s)
    s = re.sub(r"\b([A-Z]) ([a-z])(?= [A-Z])", r"\1\2", s)                     # "E l Car Wash"
    s = re.sub(r"(\w) -(\w)", r"\1-\2", s)                                       # "drive -through", "2026 -105"
    return re.sub(r"([a-z])- ([a-z])", r"\1-\2", s)


def site_addresses(addresses: str | None, text: str) -> str | None:
    """One line per property: drop 'Dr'/'Drive' duplicates, prefer the addresses the item itself names, at most two."""
    seen: dict[str, str] = {}
    for a in (addresses or "").split(";"):
        a = a.strip()
        if a and (k := norm_addr(a)) not in seen:
            seen[k] = a
        elif a and len(a) > len(seen[k]):
            seen[k] = a
    own = norm_addr(text or "")
    keep = [a for k, a in seen.items() if k in own] or list(seen.values())
    return "; ".join(keep[:2]) or None


LIQUOR_CLASS_RE = re.compile(r"\bClass[\s-]+([A-Z0-9]{1,3}(?:-[A-Z0-9]{1,3})?)\b[^.]{0,40}liquor licen[cs]e", re.I)
ADMIN_RE = re.compile(r"performance guarantee|letter of credit|\bbond (reduction|release)|\bescrow\b|recapture|"
                      r"(reduc\w*|decrease\w*)\s+(in\s+|of\s+)?the\s+number|annual renewal|renewal of", re.I)


def _company_like(name: str) -> bool:
    """A business name, not a private person: company words, or 3+ real words ("Mr. Gyo's Asian Street Food")."""
    if re.search(r"&|\bLLC\b|\bInc\b|\d|\b(Commons|Center|Centre|Plaza|Mall|Square|Park|Development|Properties|"
                 r"Shops|Studio|Golf|Wash|Signs)\b", name) or not _looks_personal(name):
        return True
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’]+", name) if w not in ("Mr", "Mrs", "Ms", "Dr")]
    return len(words) >= 3


def business(text: str, extra: str = "") -> str:
    """The business behind a lead, from the agenda wording: 'Akshar19, LLC — new liquor license (Class B)'."""
    who = ""
    for source in (text, extra):
        for rx in NAME_RES:
            m = rx.search(source or "")
            if m and _company_like(m.group(1).strip(" ,")):
                who = m.group(1).strip(" ,")
                if who.isupper() and len(who) > 4:
                    who = who.title()
                break
        if who:
            break
    if not who:
        m = WHO_RE.search(text)
        if m:
            cand = next(g for g in m.groups() if g).strip(" ,")
            if not _looks_personal(cand) or re.search(r"&|\bLLC\b|\bInc\b", cand):   # homeowners stay anonymous
                who = cand
    what = ""
    lic = LIQUOR_CLASS_RE.search(text) or LIQUOR_CLASS_RE.search(extra or "")
    if re.search(r"liquor licen[cs]e", text, re.I):
        what = f"new liquor license (Class {lic.group(1)})" if lic else "new liquor license"
    else:
        for rx in WHAT_RES:
            w = rx.search(text)
            if w:
                what = w.group(1).strip().lower()
                break
    if who and not what:
        x = re.search(r"\b(fa[cç]ade|sign|signage|exterior) (revision|renovation|improvement|change|plan)s?\b", text, re.I)
        if x:
            what = x.group(0).lower().replace("facade", "façade")
    if who and not what and re.search(r"Planned (Unit )?Development", text) and re.search(r"amend", text, re.I):
        what = "final plan amendment" if re.search(r"final plan", text, re.I) else "amendment"
    label = " — ".join(x for x in (who, what) if x)
    if label:
        return label[:1].upper() + label[1:]
    kinds = [k for k, rx in KINDS if re.search(rx, text, re.I)]
    if "planned development" in kinds and "special use" in kinds:
        kinds.remove("special use")        # "a Special Use Permit in the Nature of a Planned Development"
    if len(kinds) >= 2:                   # one kind alone ("a variation") says less than the agenda wording
        if re.search(r"\bamend", text, re.I) and kinds[0] in ("planned development", "special use", "conditional use"):
            kinds[0] += " amendment"
        kinds = kinds[:3]
        s = kinds[0] if len(kinds) == 1 else ", ".join(kinds[:-1]) + " and " + kinds[-1]
        return s[:1].upper() + s[1:]
    # fall back to the item text, minus the legal preamble ("Ordinance No. 26-O-66: An Ordinance ...")
    s = redact(re.sub(r"\s+", " ", text).strip())
    s = re.sub(r"^.{0,80}?\b(?:an ordinance|a resolution)\b[\s:.,\-–—]*", "", s, flags=re.I) or s
    s = LEGAL_PREFIX_RE.sub("", s) or s
    if s.isupper():
        s = s.capitalize()
    return s[:70].rsplit(" ", 1)[0] + ("…" if len(s) > 70 else "")


def assess(e: dict) -> dict:
    """Priority, scope, size fit and entry path for one case."""
    main, t = e["text"], e.get("all_text") or e["text"]
    approved = (e["action"] or "").startswith(("approved", "recommended_approval")) or \
        bool(re.search(r"\b(approved|granted)\b", main, re.I))

    def big(s: str) -> bool:
        return any(int(a) >= 50 for a, _, _ in BIG_SF_RE.findall(s))

    # the item's own words decide first; the rest of the packet only fills gaps
    if GROUND_UP_RE.search(main) or big(main):
        kind = "new"
    elif INTERIOR_RE.search(main) or NEW_OPERATOR_RE.search(main):
        kind = "interior"
    elif GROUND_UP_RE.search(t) or big(t):
        kind = "new"
    elif INTERIOR_RE.search(t) or NEW_OPERATOR_RE.search(t):
        kind = "interior"
    elif EXTERIOR_RE.search(t):
        kind = "exterior"
    else:
        kind = "unknown"

    if kind == "new":
        scope, fit, prio = "New building or major redevelopment", "Likely over $2M", "C"
        entry = "Watch for the tenant list; chains bring their own GCs, but local tenants inside will not."
    elif kind == "interior":
        scope, fit = "Interior build-out in an existing space", "Yes"
        prio = "A" if (approved or NEW_OPERATOR_RE.search(t)) and not e["upcoming"] else "B"
        entry = ("Visit or call the business owner now; owner-operators hire local GCs."
                 if prio == "A" else "Attend or watch the hearing; contact the applicant once approved.")
    elif kind == "exterior":
        scope, fit, prio = "Exterior, façade or sign work; often a center leasing up", "Yes (per tenant)", "B"
        entry = "Ask the property owner or manager which tenants are coming and who builds their spaces."
    else:
        scope, fit, prio = "Check the packet for scope", "Check", "B"
        entry = "Get the applicant and architect from the packet."
    return {"priority": prio, "scope": scope, "fit": fit, "entry": entry}


def gather(con, towns: list[str], end: date, days: int = 14, ahead: int = 21) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {p: [] for p, _ in PRIORITIES}
    for e in collect_cases(con, towns, end, days, ahead, classify, ["lead"], needs_place=frozenset({"lead"})):
        for k in ("text", "all_text", "summary"):
            e[k] = ocr_fix(e.get(k) or "")
        if HOUSE_RE.search(e["text"]) and not STRONG_COMMERCIAL_RE.search(e["text"]):
            continue                          # the case itself is about a house; other pages only mentioned shops
        e["addresses"] = site_addresses(e["addresses"], e["text"])
        own = category(e["text"])
        e["category"] = own if own != "Commercial" else category(e.get("all_text") or e["text"])
        e["business"] = business(e["text"], e.get("all_text", ""))
        e.update(assess(e))
        out[e["priority"]].append(e)
    for p in out:
        out[p].sort(key=lambda e: (e["town"], e["date"] or ""))
    return out


def _signal(e: dict, names: dict[str, str]) -> str:
    return f"{_status(e)} — {e['body'] or 'meeting'}, {e['date']}"


def _clean(s: str | None) -> str:
    return re.sub(r"[\t\r\n|]+", " ", s or "").strip()


def to_tsv(data: dict, names: dict[str, str]) -> str:
    rows = []
    for p, _ in PRIORITIES:
        for e in data[p]:
            rows.append("\t".join(_clean(x) for x in (
                e["category"], e["business"], names.get(e["town"], e["town"]), e["addresses"] or "",
                _signal(e, names), e["scope"], e["fit"], e["entry"], e["priority"], e["url"])))
    return "\n".join(rows) + ("\n" if rows else "")


def to_markdown(data: dict, names: dict[str, str], end: date, days: int, ahead: int) -> str:
    total = sum(len(v) for v in data.values())
    lines = [f"# Lake County commercial build-out leads — week ending {end:%B %-d, %Y}", "",
             f"Village board, plan commission and zoning agendas and minutes from {end - timedelta(days=days):%b %-d} "
             f"to {end:%b %-d}, plus agendas posted for the next {ahead} days, across {len(names)} towns. "
             f"{total} leads. Private commercial projects only; homeowner names are withheld.", ""]
    for p, title in PRIORITIES:
        items = data[p]
        lines += [f"## {title} ({p}) — {len(items)}", ""]
        if not items:
            lines += ["_Nothing this week._", ""]
            continue
        lines += ["| Lead | Where | Signal | Why it fits |", "| --- | --- | --- | --- |"]
        for e in items:
            where = ", ".join(x for x in (_clean(e["addresses"]), names.get(e["town"], e["town"])) if x)
            lines.append(f"| **{_clean(e['business'])}** ({e['category']}) | {where} | {_clean(_signal(e, names))} "
                         f"| {_clean(e['scope'])}. {_clean(e['entry'])} [source]({e['url']}) |")
        lines.append("")
    return "\n".join(lines) + "\n"


def to_html(data: dict, names: dict[str, str], end: date, days: int, ahead: int) -> str:
    esc = html.escape
    total = sum(len(v) for v in data.values())
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>Commercial leads {end.isoformat()}</title>"
             f"<style>{CSS} table{{border-collapse:collapse;width:100%;font-size:14px}}"
             "td,th{border-bottom:1px solid #eef1f5;padding:6px 8px;text-align:left;vertical-align:top}"
             "th{color:#5b6475;font-weight:600}</style></head><body>",
             f"<h1>Lake County commercial build-out leads — week ending {esc(f'{end:%B %-d, %Y}')}</h1>",
             f"<p class='meta'>Agendas and minutes from {end - timedelta(days=days):%b %-d} to {end:%b %-d}, plus agendas "
             f"for the next {ahead} days · {len(names)} towns · {total} leads<br>Private commercial projects only. "
             "Check the linked source before acting. Homeowner names are withheld.</p>"]
    for p, title in PRIORITIES:
        items = data[p]
        parts.append(f"<h2>{esc(title)} ({p}) — {len(items)}</h2>")
        if not items:
            parts.append("<p class='empty'>Nothing this week.</p>")
            continue
        parts.append("<table><tr><th>Lead</th><th>Where</th><th>Signal</th><th>Why it fits</th></tr>")
        for e in items:
            where = ", ".join(x for x in (e["addresses"], names.get(e["town"], e["town"])) if x)
            parts.append(
                f"<tr><td><b>{esc(e['business'])}</b><br><span class='meta'>{esc(e['category'])} · {esc(e['fit'])}</span></td>"
                f"<td>{esc(where)}</td><td>{esc(_signal(e, names))}</td>"
                f"<td>{esc(e['scope'])}. {esc(e['entry'])} <a href='{esc(e['url'], quote=True)}'>source</a>"
                f"<div class='meta'>{esc(e['summary'])}</div></td></tr>")
        parts.append("</table>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def write_report(con, towns: list[str], end: date | None = None, days: int = 14, ahead: int = 21,
                 names: dict[str, str] | None = None, out_dir: Path | None = None, dated: bool = True) -> dict:
    end = end or date.today()
    names = {t: (names or {}).get(t, t.replace("-", " ").title()) for t in towns}
    out_dir = out_dir or (db.DATA / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    data = gather(con, towns, end, days, ahead)
    files = {"md": to_markdown(data, names, end, days, ahead), "html": to_html(data, names, end, days, ahead),
             "tsv": to_tsv(data, names)}
    for ext, body in files.items():
        if dated:
            (out_dir / f"commercial-{end.isoformat()}.{ext}").write_text(body)
        (out_dir / f"commercial-latest.{ext}").write_text(body)
    return {p: len(v) for p, v in data.items()}
