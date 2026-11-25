"""Split document text into agenda/minutes items and tag them.

Rule-based on purpose: transparent, free, and easy to tune. An optional LLM
pass can be layered on later for summaries; the tags here decide what's worth
sending to it.
"""
from __future__ import annotations

import re

TAGS: dict[str, str] = {
    "rezoning": r"\brezon|zoning map amendment|map amendment|reclassif",
    "special_use": r"special use|conditional use|\bsup\b",
    "variance": r"\bvarianc|\bvariation",
    "pud": r"planned unit development|\bp\.?u\.?d\.?\b|planned development",
    "annexation": r"\bannex",
    "subdivision": r"subdivision|\bplat of\b|preliminary plat|final plat|resubdivi",
    "site_plan": r"site plan|site development|architectural review|building elevations?",
    "text_amendment": r"text amendment|amend(ing|ment to)? (the )?(zoning|unified development) (code|ordinance)",
    "comp_plan": r"comprehensive plan|future land use",
    "tif_incentive": r"\btif\b|tax increment|business district|redevelopment agreement|economic incentive|sales tax (rebate|sharing)",
    "data_center": r"data cent(er|re)s?|hyperscale|server farm",
    "industrial": r"warehouse|distribution cent|\bindustrial\b|logistics|truck terminal|manufactur",
    "gas_station": r"gas station|fuel(ing)? (station|center)|motor fuel|truck stop|quik ?trip|diesel",
    "car_wash": r"car wash",
    "residential_dev": r"single[- ]family|townhom|multi[- ]family|apartments?\b|residential development|dwelling units|senior living|age[- ]restricted",
    "commercial_dev": r"\bretail\b|restaurant|drive[- ]?(through|thru)|shopping cent|\bhotel\b|commercial development|\boutlot",
    "cannabis": r"cannabis|dispensar",
    "liquor_gaming": r"liquor|video gaming|gaming terminal",
    "energy_telecom": r"substation|transmission line|solar (farm|energy|garden|facility)|battery (energy )?storage|cell tower|wireless (tower|facility|telecommunication)",
    "roads": r"road (improvement|reconstruction|resurfacing)|widening|intersection improvement|traffic signal|\bidot\b|\blcdot\b|division of transportation",
    "taxes_budget": r"tax levy|\blevy\b|\bbudget\b|truth in taxation|\bbonds?\b|appropriation",
    "intergovernmental": r"intergovernmental agreement|\biga\b",
    "public_hearing": r"public hearing",
    "demolition": r"demolition|tear ?down",
    "moratorium": r"moratorium",
}
_TAGS = {k: re.compile(v, re.I) for k, v in TAGS.items()}
LAND_USE = {"rezoning", "special_use", "variance", "pud", "annexation", "subdivision", "site_plan",
            "text_amendment", "comp_plan", "data_center", "industrial", "gas_station", "car_wash",
            "residential_dev", "commercial_dev", "cannabis", "energy_telecom", "demolition", "moratorium"}

ACTIONS = [
    ("recommended_denial", r"recommend(ed|s)?\s+(a\s+)?(denial|against|rejection)"),
    ("recommended_approval", r"recommend(ed|s)?\s+(a\s+)?(approval|in favor)"),
    ("denied", r"\b(denied|motion failed|failed to pass|rejected|not approved)\b"),
    ("withdrawn", r"\bwithdr[ae]wn?\b"),
    ("tabled", r"\btabled\b"),
    ("continued", r"\bcontinued\b|\bcontinuance\b"),
    ("approved", r"\b(approved|motion (carried|passed)|adopted|carried unanimously|passed)\b"),
]
_ACTIONS = [(name, re.compile(rx, re.I)) for name, rx in ACTIONS]

ITEM_START = re.compile(
    r"^\s*(?P<num>(?:\d{1,2}(?:\.\d{1,2}){0,2})|[A-Z]|[IVXL]{1,5}|[a-z])[\.\)]\s+(?P<rest>\S.*)$"
)
ADDR_RE = re.compile(
    r"\b\d{2,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][A-Za-z']+\.?\s+){1,4}"
    r"(?:Road|Rd|Street|St|Avenue|Ave|Drive|Dr|Lane|Ln|Court|Ct|Boulevard|Blvd|Highway|Hwy|Parkway|Pkwy|Way|Trail|Trl|Circle|Cir|Place|Pl)\b\.?"
)
PIN_RE = re.compile(r"\b\d{2}-\d{2}-\d{3}-\d{3}\b")
VOTE_RE = re.compile(r"(?:vote|voting|roll call)\D{0,15}?(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\b", re.I)
_STOP = r"(?=\bNAYS?\b|\bABSENT\b|\bABSTAIN|\bMOTION\b|\n|$)"
AYES_RE = re.compile(rf"\bAYES?\s*[:\-]\s*(.+?){_STOP}", re.I)
NAYS_RE = re.compile(rf"\bNAYS?\s*[:\-]\s*(.+?){_STOP}", re.I)


def tags_for(text: str) -> list[str]:
    return [k for k, rx in _TAGS.items() if rx.search(text)]


def _count_names(s: str) -> int:
    s = s.strip()
    if re.match(r"^(none|0|n/a)\b", s, re.I):
        return 0
    m = re.match(r"^\(?(\d{1,2})\)?\b", s)
    if m:
        return int(m.group(1))
    return len([p for p in re.split(r",|\band\b", s) if p.strip()])


def vote_for(text: str) -> str | None:
    m = VOTE_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    a, n = AYES_RE.search(text), NAYS_RE.search(text)
    if a:
        return f"{_count_names(a.group(1))}-{_count_names(n.group(1)) if n else 0}"
    return None


def action_for(text: str, is_minutes: bool) -> str | None:
    if not is_minutes:
        return None
    for name, rx in _ACTIONS:
        if rx.search(text):
            return name
    return None


def split_items(text: str, max_len: int = 2500) -> list[dict]:
    """Numbered agenda lines start a new item; following lines append to it."""
    items: list[dict] = []
    cur: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = ITEM_START.match(line)
        if m and len(m.group("rest")) > 3:
            if cur:
                items.append(cur)
            cur = {"number": m.group("num"), "text": m.group("rest")}
        elif cur is not None:
            if len(cur["text"]) < max_len:
                cur["text"] += " " + line
    if cur:
        items.append(cur)
    # drop pure boilerplate
    boiler = re.compile(r"^(call to order|roll call|pledge|adjourn|approval of (the )?(agenda|minutes)|public comment|"
                        r"executive session|trustee comments|mayor'?s comments|old business|new business)\W*$", re.I)
    return [dict(seq=i, **it) for i, it in enumerate(items) if not boiler.match(it["text"].strip())]


def analyze_item(text: str, is_minutes: bool) -> dict:
    tags = tags_for(text)
    return {
        "tags": ",".join(tags) or None,
        "action": action_for(text, is_minutes),
        "vote": vote_for(text) if is_minutes else None,
        "addresses": "; ".join(sorted(set(a.strip(" .") for a in ADDR_RE.findall(text)))) or None,
        "pins": ", ".join(sorted(set(PIN_RE.findall(text)))) or None,
    }


def normalize_action(raw: str | None) -> str | None:
    """Map Legistar action strings onto our action vocabulary."""
    if not raw:
        return None
    for name, rx in _ACTIONS:
        if rx.search(raw):
            return name
    return "discussed"
