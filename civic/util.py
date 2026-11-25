"""Link, date, body and document-type heuristics shared by the adapters."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import unquote, urljoin, urlparse, urldefrag

DOC_EXTS = (".pdf", ".doc", ".docx")
DOC_URL_PATTERNS = [
    r"/AgendaCenter/ViewFile/",        # CivicPlus
    r"/DocumentCenter/View/",          # CivicPlus
    r"/Archive\.aspx\?ADID=",          # CivicPlus archive center
    r"docview\.aspx",                  # MunicipalCMS
    r"/media/\d+",                     # Drupal media (Long Grove)
    r"AgendaViewer\.php", r"MinutesViewer\.php", r"MetaViewer\.php",  # Granicus
    r"GetMeetingFileStream",           # CivicClerk
    r"/View\.ashx\?M=",                # Legistar
    r"/frm_file/",                     # WordPress Formidable Forms uploads (Hainesville)
    r"djcatalog2/.*task=download",     # Joomla DJ-Catalog2 (Lake Bluff, Lincolnshire, Beach Park)
    r"/Download\b", r"/download\b",
]
_DOC_RE = re.compile("|".join(DOC_URL_PATTERNS), re.I)

KEYWORDS_DOC = re.compile(
    r"agenda|minutes|packet|hearing|notice|ordinance|resolution|meeting|board|commission|committee|zoning|planning|council",
    re.I,
)
KEYWORDS_PAGE = re.compile(
    r"agenda|minute|meeting|board|commission|committee|council|hearing|government|documents?|archive|"
    r"zoning|planning|plan-commission|notice|trustees|clerk|development|projects",
    re.I,
)
SKIP_PAGE = re.compile(
    r"facebook\.com|twitter\.com|x\.com/|instagram\.com|youtube\.com|linkedin\.com|mailto:|tel:|"
    r"/calendar|/event|login|signin|/rss|\.(jpg|jpeg|png|gif|zip|mp4|mp3|xlsx?|csv|ics)(\?|$)",
    re.I,
)

BODIES = [
    (r"zoning board of appeals|\bzba\b", "Zoning Board of Appeals"),
    (r"plan(ning)? commission|planning (and|&) zoning|plan commission|\bpczba\b|\bpzc\b|\bpzb\b|plan & zoning", "Plan Commission"),
    (r"committee of the whole|\bcow\b", "Committee of the Whole"),
    (r"architectur", "Architectural Commission"),
    (r"economic development", "Economic Development Commission"),
    (r"county board", "County Board"),
    (r"pbz|planning, building|building, zoning", "PBZ&E Committee"),
    (r"city council", "City Council"),
    (r"village board|board of trustees|regular (board )?meeting|special (board )?meeting", "Village Board"),
    (r"park", "Park Board"),
    (r"police (and|&) fire|fire (and|&) police", "Police & Fire Commission"),
    (r"pension", "Pension Board"),
    (r"finance", "Finance Committee"),
    (r"public works", "Public Works Committee"),
    (r"historic|preservation", "Historic Preservation Commission"),
    (r"conservancy|scenic corridor", "Conservancy Committee"),
]
_BODIES = [(re.compile(p, re.I), name) for p, name in BODIES]

DOC_TYPES = [
    (r"packet", "packet"),
    (r"minute", "minutes"),
    (r"agenda", "agenda"),
    (r"public hearing|legal notice|notice of", "notice"),
    (r"ordinance", "ordinance"),
    (r"resolution", "resolution"),
]
_DOC_TYPES = [(re.compile(p, re.I), t) for p, t in DOC_TYPES]

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_DATE_PATTERNS = [
    # 2026-11-17
    (re.compile(r"(?<!\d)(20\d{2}|19\d{2})[-_.](\d{1,2})[-_.](\d{1,2})(?!\d)"), "ymd"),
    # November 17, 2026 / Nov. 17 2026
    (re.compile(rf"\b({MONTHS})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I), "mdy_name"),
    # 17 November 2026
    (re.compile(rf"\b(\d{{1,2}})\s+({MONTHS})[a-z]*\.?,?\s+(\d{{4}})\b", re.I), "dmy_name"),
    # CivicPlus ViewFile _11172026
    (re.compile(r"_(\d{2})(\d{2})(\d{4})(?:-|\b)"), "mdy_compact"),
    # 11/17/2026, 11-17-26, 11.17.2026
    (re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})(?!\d)"), "mdy"),
]
_MONTH_NUM = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def _mk(y: int, m: int, d: int) -> str | None:
    if y < 100:
        y += 2000 if y < 70 else 1900
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    if not (1990 <= dt.year <= date.today().year + 2):
        return None
    return dt.isoformat()


def parse_date(*texts: str) -> str | None:
    """First plausible meeting date found in the given strings (link text first, then URL)."""
    for raw in texts:
        if not raw:
            continue
        s = unquote(raw)
        for rx, kind in _DATE_PATTERNS:
            for m in rx.finditer(s):
                g = m.groups()
                if kind == "ymd":
                    iso = _mk(int(g[0]), int(g[1]), int(g[2]))
                elif kind == "mdy_name":
                    iso = _mk(int(g[2]), _MONTH_NUM[g[0][:3].lower()], int(g[1]))
                elif kind == "dmy_name":
                    iso = _mk(int(g[2]), _MONTH_NUM[g[1][:3].lower()], int(g[0]))
                else:  # mdy, mdy_compact
                    iso = _mk(int(g[2]), int(g[0]), int(g[1]))
                if iso:
                    return iso
    return None


def doc_type(*texts: str) -> str:
    s = " ".join(unquote(t or "") for t in texts)
    for rx, t in _DOC_TYPES:
        if rx.search(s):
            return t
    return "other"


def body_name(*texts: str) -> str | None:
    s = " ".join(unquote(t or "") for t in texts)
    for rx, name in _BODIES:
        if rx.search(s):
            return name
    return None


def normalize(url: str, base: str | None = None) -> str:
    u = urljoin(base, url) if base else url
    u, _ = urldefrag(u)
    return u.strip()


def is_doc_link(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(DOC_EXTS) or bool(_DOC_RE.search(url))


def looks_civic_doc(url: str, text: str) -> bool:
    """A document link worth keeping: a document AND mentions civic keywords (or a date)."""
    if not is_doc_link(url):
        return False
    s = f"{text} {unquote(url)}"
    return bool(KEYWORDS_DOC.search(s) or parse_date(text, url))


def host_root(host: str) -> str:
    parts = host.lower().split(".")
    if parts and parts[0] == "www":
        parts = parts[1:]
    return ".".join(parts[-3:]) if len(parts) >= 3 and parts[-2] in ("il", "co") else ".".join(parts[-2:])


def same_site(url: str, home: str) -> bool:
    a, b = urlparse(url).netloc, urlparse(home).netloc
    return bool(a) and host_root(a) == host_root(b)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
