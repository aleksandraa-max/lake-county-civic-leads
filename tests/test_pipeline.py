"""Offline end-to-end tests: a fake web of village sites, run the whole pipeline."""
from __future__ import annotations

import io
import json
import time
from datetime import date

import pytest
from reportlab.pdfgen import canvas

from civic import commercial, db, pipeline, report
from civic.adapters import civicplus, generic, legistar, civicclerk
from civic.classify import analyze_item, split_items, vote_for
from civic.http import Resp
from civic.util import parse_date, doc_type, body_name, is_doc_link

YEAR = date.today().year


def make_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 16
    c.save()
    return buf.getvalue()


AGENDA_LINES = [
    "VILLAGE OF TESTVILLE PLAN COMMISSION AGENDA",
    f"Tuesday, November 4, {YEAR} 7:00 PM",
    "1. Call to Order",
    "2. Roll Call",
    "3. Public Hearing: Special Use Permit and Planned Unit Development for a QuikTrip",
    "   motor fuel station with diesel bays at 21500 N Rand Road, PIN 14-30-200-012.",
    "4. Variance request for a fence at 123 Oak Lane.",
    "5. Discussion of a proposed data center on the Smith farm, 400 acres.",
    "6. Adjourn",
] + ["Additional text so the document is long enough to count as real text."] * 6

MINUTES_LINES = [
    "VILLAGE OF TESTVILLE BOARD OF TRUSTEES MINUTES",
    f"November 10, {YEAR}",
    "1. Call to Order",
    "2. Ordinance approving a Special Use Permit for QuikTrip at 21500 N Rand Road.",
    "   Motion carried. AYES: Smith, Jones, Lee, Park NAYS: Brown, Diaz",
    "3. Resolution approving the tax levy. Motion carried unanimously.",
] + ["Additional text so the document is long enough to count as real text."] * 6

HOME = f"""<html><body>
<a href="/government/agendas-minutes">Agendas &amp; Minutes</a>
<a href="https://facebook.com/testville">Facebook</a>
<a href="/parks">Parks</a>
</body></html>"""

LISTING = f"""<html><body><table>
<tr><td>November 4, {YEAR}</td><td>Plan Commission</td><td><a href="/files/pc-agenda.pdf">Agenda</a></td></tr>
<tr><td>November 10, {YEAR}</td><td>Village Board</td><td><a href="/files/vb-minutes-11-10-{YEAR}.pdf">Minutes</a></td></tr>
<tr><td>March 3, 2019</td><td>Village Board</td><td><a href="/files/old-agenda-2019.pdf">Agenda</a></td></tr>
<tr><td><a href="/files/brochure.pdf">Summer brochure</a></td></tr>
</table></body></html>"""

CP_LANDING = f"""<html><body>Government Websites by CivicPlus
<a href="/AgendaCenter/Plan-Commission-5">Plan Commission</a>
<a href="/AgendaCenter/Village-Board-8">Village Board</a>
</body></html>"""

CP_LIST = f"""<table><tr><td><strong>Nov 4, {YEAR}</strong>
<a href="/AgendaCenter/ViewFile/Agenda/_1104{YEAR}-321">Plan Commission Agenda</a>
<a href="/AgendaCenter/ViewFile/Minutes/_1104{YEAR}-321">Minutes</a></td></tr></table>"""

LEGISTAR_EVENTS = [{"EventId": 77, "EventBodyName": "County Board", "EventDate": f"{YEAR}-09-08T00:00:00",
                    "EventAgendaFile": "https://lakecounty.legistar.com/View.ashx?M=A&ID=77",
                    "EventMinutesFile": None, "EventInSiteURL": "https://lakecounty.legistar.com/MeetingDetail.aspx?ID=77"}]
LEGISTAR_ITEMS = [
    {"EventItemTitle": "Ordinance establishing an eight-month moratorium on data centers in unincorporated Lake County",
     "EventItemActionName": "approved", "EventItemPassedFlagName": "Pass", "EventItemAgendaNumber": "8.3",
     "EventItemTally": "19-0", "EventItemMatterType": "Ordinance"},
    {"EventItemTitle": "", "EventItemActionName": None},
]
CIVICCLERK = {"value": [{"id": 5, "eventName": "Plan Commission", "startDateTime": f"{YEAR}-10-01T19:00:00Z",
                         "categoryName": "Plan Commission",
                         "publishedFiles": [{"fileId": 991, "type": "Agenda", "name": "Agenda"}]}]}

RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Testville board approves QuikTrip despite opposition - Daily Herald</title>
<link>https://news.example/quiktrip</link><pubDate>Tue, 11 Nov {YEAR} 10:00:00 GMT</pubDate>
<source url="https://dailyherald.com">Daily Herald</source></item>
<item><title>Unrelated story about another town - Patch</title>
<link>https://news.example/other</link><pubDate>Tue, 11 Nov {YEAR} 10:00:00 GMT</pubDate></item>
</channel></rss>"""


class FakeFetcher:
    def __init__(self, routes: dict):
        self.routes = routes
        self.requests_made = 0
        self.calls: list[str] = []

    def get(self, url: str, **kw) -> Resp:
        self.requests_made += 1
        self.calls.append(url)
        for key, val in self.routes.items():
            if url == key or url.rstrip("/") == key.rstrip("/") or (key.endswith("*") and url.startswith(key[:-1])):
                body, ctype = val
                return Resp(url, 200, body if isinstance(body, bytes) else body.encode(), {"content-type": ctype})
        return Resp(url, 404, b"", {})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "data")
    monkeypatch.setattr(db, "TEXT_DIR", tmp_path / "data" / "text")
    (tmp_path / "data").mkdir()
    con = db.connect(tmp_path / "data" / "civic.db")
    routes = {
        "https://testville.example": (HOME, "text/html"),
        "https://testville.example/government/agendas-minutes": (LISTING, "text/html"),
        "https://testville.example/files/pc-agenda.pdf": (make_pdf(AGENDA_LINES), "application/pdf"),
        f"https://testville.example/files/vb-minutes-11-10-{YEAR}.pdf": (make_pdf(MINUTES_LINES), "application/pdf"),
        "https://cpville.example": (CP_LANDING, "text/html"),
        "https://cpville.example/AgendaCenter": (CP_LANDING, "text/html"),
        "https://cpville.example/AgendaCenter/UpdateCategoryList*": (CP_LIST, "text/html"),
        f"https://cpville.example/AgendaCenter/ViewFile/Agenda/_1104{YEAR}-321": (make_pdf(AGENDA_LINES), "application/pdf"),
        "https://news.google.com/rss/search*": (RSS, "application/rss+xml"),
    }
    return con, FakeFetcher(routes), tmp_path


TOWNS = [
    {"slug": "testville", "name": "Testville", "kind": "village", "url": "https://testville.example"},
    {"slug": "cpville", "name": "CPville", "kind": "village", "url": "https://cpville.example", "platform": "civicplus"},
]


# ------------------------------------------------------------------ units
def test_parse_date_variants():
    assert parse_date("Agenda 11-17-2026") == "2026-11-17"
    assert parse_date("November 17, 2026 Village Board") == "2026-11-17"
    assert parse_date("/AgendaCenter/ViewFile/Agenda/_11172026-1234") == "2026-11-17"
    assert parse_date("2026-11-17 minutes") == "2026-11-17"
    assert parse_date("01-15-25 Village Board And COW Agenda") == "2025-01-15"
    assert parse_date("Ordinance 2026-O-14") is None or parse_date("Ordinance 2026-O-14").startswith("2026") is False


def test_doc_link_patterns():
    assert is_doc_link("https://www.hainesville.org/frm_file/aWQ6Njg2NTd8ZmlsZW5hbWU6MTEtMTctMjYtQWdlbmRhLnBkZg==")
    assert is_doc_link("https://www.lakebluff.org/component/djcatalog2/?format=raw&task=download&fid=4704")
    assert is_doc_link("https://lakecounty.legistar.com/View.ashx?M=A&ID=1465702&GUID=FF42F5F6")
    assert not is_doc_link("https://www.lakebluff.org/component/djcatalog2/items/1-agendas")


def test_doc_type_and_body():
    assert doc_type("Village Board Agenda") == "agenda"
    assert doc_type("PCZBA Packet 2026") == "packet"
    assert body_name("PCZBA agenda") == "Plan Commission"
    assert body_name("Committee of the Whole Agenda") == "Committee of the Whole"


def test_split_and_tag():
    text = "\n".join(AGENDA_LINES)
    items = split_items(text)
    texts = [i["text"] for i in items]
    assert not any(t.startswith("Call to Order") for t in texts)
    qt = next(i for i in items if "QuikTrip" in i["text"])
    a = analyze_item(qt["text"], is_minutes=False)
    assert {"special_use", "pud", "gas_station", "public_hearing"} <= set(a["tags"].split(","))
    assert "21500 N Rand Road" in a["addresses"]
    assert a["pins"] == "14-30-200-012"
    assert a["action"] is None  # agendas don't carry outcomes
    dc = next(i for i in items if "data center" in i["text"])
    assert "data_center" in analyze_item(dc["text"], False)["tags"]


def test_minutes_action_and_vote():
    a = analyze_item("Ordinance approving QuikTrip. Motion carried. AYES: Smith, Jones, Lee, Park NAYS: Brown, Diaz", True)
    assert a["action"] == "approved"
    assert a["vote"] == "4-2"
    assert vote_for("Roll call vote 4-2") == "4-2"
    assert analyze_item("The commission recommended denial of the petition.", True)["action"] == "recommended_denial"


def test_legistar_items():
    items = legistar.event_items(LEGISTAR_ITEMS)
    assert len(items) == 1 and items[0]["vote"] == "19-0" and items[0]["number"] == "8.3"


def test_civicclerk_parse():
    docs = civicclerk.parse_events(CIVICCLERK, "testtenant")
    assert docs[0].doc_type == "agenda" and docs[0].meeting_date == f"{YEAR}-10-01"
    assert "GetMeetingFileStream(fileId=991" in docs[0].url


def test_generic_crawl_filters(env):
    con, f, _ = env
    res = generic.crawl(TOWNS[0], f, since_iso=f"{YEAR - 3}-01-01")
    urls = {d.url for d in res.docs}
    assert "https://testville.example/files/pc-agenda.pdf" in urls
    assert not any("old-agenda-2019" in u for u in urls)          # before `since`
    assert not any("brochure" in u for u in urls)                # not a civic doc
    assert not any("facebook" in c for c in f.calls)             # never crawled social links
    pc = next(d for d in res.docs if d.url.endswith("pc-agenda.pdf"))
    assert pc.meeting_date == f"{YEAR}-11-04" and pc.body == "Plan Commission" and pc.doc_type == "agenda"


def test_civicplus_categories(env):
    con, f, _ = env
    res = civicplus.collect(TOWNS[1], f, since_iso=f"{YEAR}-01-01", max_pages=10)
    kinds = {(d.doc_type, d.meeting_date) for d in res.docs}
    assert ("agenda", f"{YEAR}-11-04") in kinds and ("minutes", f"{YEAR}-11-04") in kinds
    assert any(d.body == "Plan Commission" for d in res.docs)


# ------------------------------------------------------------- end to end
def test_end_to_end(env):
    con, f, tmp = env
    deadline = time.monotonic() + 60
    pipeline.discover(con, f, TOWNS)
    disc = json.loads((tmp / "data" / "discovery.json").read_text())
    assert {r["slug"]: r["platform"] for r in disc}["cpville"] == "civicplus"

    pipeline.collect(con, f, TOWNS, since=f"{YEAR - 3}-01-01", deadline=deadline)
    n_docs = con.execute("SELECT count(*) FROM documents").fetchone()[0]
    assert n_docs >= 4

    pipeline.news(con, f, TOWNS)
    titles = [r[0] for r in con.execute("SELECT title FROM news")]
    assert any("QuikTrip" in t for t in titles)
    assert not any("Unrelated" in t for t in titles)          # must name the town
    assert all(not t.endswith("Daily Herald") for t in titles)  # publisher stripped

    stats = pipeline.extract(con, f, deadline)
    assert stats["ok"] >= 3
    pipeline.classify(con)

    row = con.execute("""SELECT action, vote, tags FROM items WHERE town='testville'
                         AND text LIKE '%QuikTrip%' AND action IS NOT NULL""").fetchone()
    assert row["action"] == "approved" and row["vote"] == "4-2" and "gas_station" in row["tags"]

    # re-running is idempotent
    before = con.execute("SELECT count(*) FROM items").fetchone()[0]
    pipeline.collect(con, f, TOWNS, since=f"{YEAR - 3}-01-01", deadline=deadline)
    pipeline.classify(con)
    assert con.execute("SELECT count(*) FROM items").fetchone()[0] == before

    pipeline.export(con)
    status = (tmp / "data" / "STATUS.md").read_text()
    assert "Testville" in status and "CPville" in status
    digest = (tmp / "data" / "digests" / "testville.md").read_text()
    assert "QuikTrip" in digest and "approved" in digest
    csv_text = (tmp / "data" / "exports" / "land_use_items.csv").read_text()
    assert "special_use" in csv_text


def test_unreachable_town_does_not_break_run(env):
    con, f, _ = env
    towns = TOWNS + [{"slug": "ghost", "name": "Ghost", "kind": "village", "url": "https://ghost.example"}]
    pipeline.discover(con, f, towns)
    summary = pipeline.collect(con, f, towns, since=f"{YEAR - 3}-01-01", deadline=time.monotonic() + 60)
    assert summary["ghost"]["found"] == 0
    assert con.execute("SELECT status FROM towns WHERE slug='ghost'").fetchone()[0] == "unreachable"
    assert summary["testville"]["found"] >= 2


def test_config_is_valid():
    towns = pipeline.load_towns()
    slugs = [t["slug"] for t in towns]
    assert len(slugs) == len(set(slugs))
    assert len(towns) >= 50
    for t in towns:
        assert t["url"].startswith("https://") and t["kind"] in ("village", "city", "county")


# ------------------------------------------------------------- GC report
def test_report_sections_and_details():
    assert report.section_for("Request to demolish the existing house and build a new single-family home", set()) == "teardown"
    assert report.section_for("Variance for a rear yard setback at 12 Elm Court", {"variance"}) == "zoning"
    assert report.section_for("Text amendment raising building permit fees", set()) == "rules"
    text = ("A bid opening on February 4, 2026 yielded three (3) bids. Staff recommends awarding a contract to "
            "Visu Sewer in the amount of $100,000.")
    assert report.section_for(text, set()) == "bids"
    d = report.bid_details(text)
    assert d["amount"] == "$100,000" and d["vendor"] == "Visu Sewer" and d["bids"] == "three"


def test_report_redacts_homeowners_not_companies():
    assert "John Smith" not in report.redact("Petitioner John Smith requests a variance")
    assert "requests a variance" in report.redact("Petitioner John Smith requests a variance")
    assert "Pulte Homes" in report.redact("Applicant Pulte Homes requests preliminary plat approval")

    # Highland Park ZBA-style wording (names and address made up): "Appellant:", middle initial, "as Trustee", couples, home address
    hp = ("#VAR-2026-00000 Property: 90 Example Rd Zoning District: R4 | LFOZ Appellant: Mary T. Sample, as Trustee "
          "and Paul Sample Address: 90 Example Rd, Highland Park, IL 60035 The petitioners and owners, Mary T. Sample, "
          "as Trustee and Paul Sample of 90 Example Rd, Highland Park, request a variation")
    out = report.redact(hp)
    assert "Sample" not in out and "Mary" not in out
    assert "#VAR-2026-00000" in out and "of 90 Example Rd" in out and "request a variation" in out
    assert "Testerman" not in report.redact("Appellant: Ann and Joe Testerman Address: 112 Maple Ave")
    out = report.redact("Appellant: Sam Placeholder Address: 2271 Sample Ridge Northbrook, IL 60062 for 550 Skokie Avenue")
    assert "Placeholder" not in out and "2271 Sample Ridge" not in out and "550 Skokie Avenue" in out


def test_report_drops_packet_noise():
    # plan-sheet labels, contract fine print and OCR garbage are not leads
    assert not report.readable("RETRACTABLE SEATBELTS E 48X26 V V V V V 3 3 3 4 REV DATEBYDESCRIPTION REVISIONS "
                               "CHECKED BY CHECKED BY: OF 3 3 3 WHITE STANDEE FLOOR SPACE 48X28")
    davis = ("DAVIS-BACON ACT, AS AMENDED (40 U.S.C. 3141-3148). When required by federal program legislation, all "
             "prime construction contracts in excess of $2,000 awarded by non- federal entities must comply.")
    assert not report.readable(davis) and report.section_for(davis, set()) is None
    assert not report.readable("E><CAYATE PA�tti LOT 151..ANDS AND CUfceED PLANTE� TO A DEPTM � 2411 AND BACl<FILL")
    assert report.readable("Minutes of the regular meeting. Date: Nov 17, 2026. The board approved the variance "
                           "for a new garage.")
    # an ordinance that changes builder rules counts; plain mentions of fees without an ordinance do not
    assert report.section_for("Discussion of building permit fees collected last year", set()) is None
    assert report.section_for("An Ordinance amending Chapter 9 to increase building permit fees", set()) == "rules"


def test_report_end_to_end(env):
    con, _, tmp = env
    con.execute("INSERT INTO towns(slug,name) VALUES('deerfield','Deerfield')")
    end = date(2026, 11, 23)

    def doc(url, doc_type, meeting_date):
        return con.execute("INSERT INTO documents(town,url,doc_type,meeting_date,first_seen,fetch_status) "
                           "VALUES('deerfield',?,?,?,'2026-11-23','ok')", (url, doc_type, meeting_date)).lastrowid

    def item(doc_id, meeting_date, text, tags=None, action=None, addr=None):
        con.execute("INSERT INTO items(document_id,town,meeting_date,body,seq,text,tags,action,addresses) "
                    "VALUES(?,'deerfield',?,'Village Board',0,?,?,?,?)", (doc_id, meeting_date, text, tags, action, addr))

    teardown = "Petitioner Jane Doe requests approval to demolish the existing home at 455 Oak Street and build a new residence."
    item(doc("https://d.example/agenda.pdf", "agenda", "2026-11-17"), "2026-11-17", teardown, "demolition", None,
         "455 Oak Street")
    item(doc("https://d.example/minutes.pdf", "minutes", "2026-11-17"), "2026-11-17", teardown, "demolition", "approved",
         "455 Oak Street")                                                                     # same case, decided
    item(doc("https://d.example/zba.pdf", "agenda", "2026-12-01"), "2026-12-01",
         "Public hearing: variance for a detached garage height at 9 Elm Court in the R-1 district.",
         "variance,public_hearing", None, "9 Elm Court")                                       # upcoming
    item(doc("https://d.example/old.pdf", "agenda", "2026-09-01"), "2026-09-01",
         "Variance for a fence that is far outside the window of this report.", "variance")    # too old
    packet = doc("https://d.example/packet.pdf", "agenda", "2026-11-20")                        # a 61-item packet
    for _ in range(60):
        item(packet, "2026-11-20", "The architect describes the proposed rear elevation materials in great detail "
             "for the commission members.", "variance")                                        # page text: dropped
    item(packet, "2026-11-20", "Variance request for 77 Pine Lane to reduce the rear yard for a sunroom addition.",
         "variance", None, "77 Pine Lane")                                                      # agenda line: kept
    con.commit()

    counts = report.write_report(con, ["deerfield"], end=end, names={"deerfield": "Deerfield"}, out_dir=tmp / "reports")
    assert counts == {"teardown": 1, "bids": 0, "zoning": 2, "rules": 0}
    md = (tmp / "reports" / "gc-weekly-2026-11-23.md").read_text()
    assert "Approved" in md and "minutes.pdf" in md and "agenda.pdf" not in md   # one entry per case, the decided copy
    assert "1 related document" in md
    assert "Upcoming" in md and "Jane Doe" not in md and "fence" not in md
    assert "77 Pine Lane" in md and "architect describes" not in md
    page = (tmp / "reports" / "latest.html").read_text()
    assert "<h2>Teardowns and new homes (1)</h2>" in page and "455 Oak Street" in page


def test_report_redacts_filed_by_owners():
    out = report.redact("A petition filed by Rita Fictional and Ivo Madeup, owners, requesting variations")
    assert "Fictional" not in out and "requesting variations" in out
    assert "Home Depot USA Inc." in report.redact("A petition filed by Home Depot USA Inc., owner, requesting")
    assert "Aspen Holdings" in report.redact("A petition filed by Aspen Holdings, owner, requesting a sign variation")


# ----------------------------------------------------- commercial build-out leads
def _case(text, action=None, upcoming=False):
    return {"text": text, "all_text": text, "action": action, "upcoming": upcoming}


def test_commercial_classify_and_rank():
    pnc = ("An Ordinance granting a special use permit for a bank with a drive-up ATM within an existing building "
           "at 701 N Milwaukee Ave, Unit 354")
    assert commercial.classify(pnc, {"special_use"}, True) == "lead"
    assert commercial.category(pnc) == "Bank"
    assert commercial.assess(_case(pnc, "approved"))["priority"] == "A"

    truck = ("500 Rose Road - Special Use (2026-15) Application for a Special Use Permit to permit a truck repair shop "
             "at an existing industrial facility in the I-Industrial Zoning District.")
    assert commercial.category(truck) == "Industrial"
    assert commercial.business(truck) == "Truck repair shop"
    a = commercial.assess(_case(truck, upcoming=True))
    assert a["priority"] == "B" and a["fit"] == "Yes"

    wash = "Special use and site plan to construct a new car wash building with drive-through lanes at 1750 N Milwaukee Ave"
    assert commercial.assess(_case(wash, "approved"))["priority"] == "C"

    liquor = "Hacienda Jalisco, 7680 Grand Ave - request for a Class 1 liquor license"
    assert commercial.category(liquor) == "Restaurant"
    assert commercial.assess(_case(liquor))["priority"] == "A"

    assert "Home Depot USA Inc." in commercial.business(
        "A petition filed by Home Depot USA Inc., owner, requesting a special use permit for vehicle and equipment rental")

    # residential, public works and gaming-only items are not commercial leads
    assert commercial.classify("Variance for a fence at 12 Elm Court", {"variance"}, True) is None
    assert commercial.classify("Award of contract for water main replacement on Main Street to Visu Sewer",
                               set(), True) is None
    assert commercial.classify("Video gaming terminal license renewal for Lucky Stop", {"liquor_gaming"}, True) is None


def test_commercial_end_to_end_tsv(env):
    con, _, tmp = env
    con.execute("INSERT INTO towns(slug,name) VALUES('vernon-hills','Vernon Hills')")
    end = date(2026, 11, 23)
    cur = con.execute("INSERT INTO documents(town,url,doc_type,meeting_date,first_seen,fetch_status) "
                      "VALUES('vernon-hills','https://vh.example/min.pdf','minutes','2026-11-17','2026-11-23','ok')")
    con.execute("INSERT INTO items(document_id,town,meeting_date,body,seq,text,tags,action,addresses) "
                "VALUES(?,'vernon-hills','2026-11-17','Village Board',0,?,'special_use','approved','701 N Milwaukee Ave')",
                (cur.lastrowid, "An Ordinance granting a special use permit for a bank with a drive-up ATM within an "
                                "existing building at 701 N Milwaukee Ave, Unit 354"))
    con.commit()
    counts = commercial.write_report(con, ["vernon-hills"], end=end, names={"vernon-hills": "Vernon Hills"},
                                     out_dir=tmp / "reports")
    assert counts == {"A": 1, "B": 0, "C": 0}
    row = (tmp / "reports" / "commercial-latest.tsv").read_text().strip().split("\t")
    assert len(row) == 10                                   # the column order a lead-tracker spreadsheet expects
    assert row[0] == "Bank" and row[2] == "Vernon Hills" and row[3] == "701 N Milwaukee Ave" and row[8] == "A"
    assert row[9] == "https://vh.example/min.pdf"
    assert "Act this week (A) — 1" in (tmp / "reports" / "commercial-latest.md").read_text()


def test_commercial_keeps_homeowners_and_houses_out():
    fence = ("Public Hearing for Case #2026- 00. A petition filed by Rita Fictional and Ivo Madeup, owners, requesting "
             "variations from the requirements of Appendix C, Zoning, related to the restriction on parallel fences")
    assert commercial.classify(fence, {"variance"}, True) is None
    assert "Fictional" not in commercial.business(fence)
    garages = ("ORDINANCE NO. 26-O-66: An Ordinance Granting a Variation Regarding the Maximum Number of Permitted "
               "Garages - 848 Example Lane")
    assert commercial.classify(garages, {"special_use"}, True) is None
    # "dance" inside "accordance" is not a dance studio
    assert commercial.category("in accordance with the code, a new office for an accounting firm") != "Fitness"
    # a daycare in a residential district is still a commercial lead
    assert commercial.classify("Special use for a child care center in the R-3 Residential District at 10 Oak Ave",
                               {"special_use"}, True) == "lead"


def test_commercial_label_skips_legal_preamble():
    label = commercial.business("ORDINANCE NO. 26-O-66: An Ordinance Granting a Variation From the Sign Code for "
                                "the shopping center at 100 Main Street")
    assert label.startswith("Granting a Variation")


def test_commercial_names_and_admin_items():
    lz = ("Ordinance Amending Title 3 of Chapter 3 of the Village of Lake Zurich Municipal Code to Issue a Class-B Liquor "
          "License to Akshar19, LLC (Assign Ord. 2026-11-696) Summary: Akshar19, LLC, located at 103 S. Rand Road")
    assert commercial.business(lz) == "Akshar19, LLC — new liquor license (Class B)"
    lh = ("Ordinance 2026-11-2353: Amending Section 111.20 of the Village Code to Increase the Number of Class A-V Liquor "
          "Licenses by One – Mr. Gyo’s Asian Street Food – 2234 E. Grand Avenue")
    assert commercial.business(lh).startswith("Mr. Gyo’s Asian Street Food")
    assert commercial.category(lh) == "Restaurant"
    # a person between dashes is not shown
    assert "Jane Roe" not in commercial.business("Liquor license transfer – Jane Roe – 12 Main Street, Unit 3")
    # in an existing unit: an interior build-out, even if the packet elsewhere mentions a hotel
    golf = ("Request for a Class B Special Use to permit a self-improvement facility at 710 Robert York Avenue, Unit C - "
            "Back Nine Golf and CRM Properties - continued")
    case = {"text": golf, "all_text": golf + " near the hotel", "action": None, "upcoming": False}
    assert commercial.assess(case)["scope"].startswith("Interior")
    assert commercial.business(golf).startswith("Back Nine Golf and CRM Properties")
    # administrative items are not leads
    for admin in ("An Ordinance Authorizing the Reduction of the Performance Guarantee for the Raising Cane's Development",
                  "An Ordinance relating to liquor licenses to reflect the automatic reduction in the number of Class B "
                  "liquor licenses"):
        assert commercial.classify(admin, {"commercial_dev"}, True) is None
    assert commercial.classify("Resolution approving a development agreement – Example Subdivision – 245 Sample Street",
                               {"subdivision"}, True) is None


def test_commercial_drops_house_cases_from_packets():
    garage = ("ORDINANCE NO. 26-O-66: An Ordinance Granting a Variation From 26-10-1.5(c)(5)(c) of the Libertyville Zoning "
              "Code Regarding the Maximum Number of Permitted Garages – 848 Example Lane")
    assert commercial.HOUSE_RE.search(garage) and not commercial.STRONG_COMMERCIAL_RE.search(garage)
    assert commercial.STRONG_COMMERCIAL_RE.search("a fence around the outdoor dining area of the restaurant")


def test_commercial_ocr_fix():
    fix = commercial.ocr_fix
    assert fix("requests a co nditional use for a recycl e facility") == "requests a conditional use for a recycle facility"
    assert fix("FOR A CAR WASH, EL CAR WA SH, TO BE LOCATED") == "FOR A CAR WASH, EL CAR WASH, TO BE LOCATED"
    assert fix("a drive -through restaurant, Ordinance 2026 -105") == "a drive-through restaurant, Ordinance 2026-105"
    for keep in ("in order to be at the site", "Unit C and B-2 District", "SECTION IV OF THE CODE",
                 "the WAUCONDA IL office", "5,000 sq ft of space"):
        assert fix(keep) == keep


def test_commercial_site_addresses():
    text = "Address: 280 Industrial Drive, Wauconda IL 60084 Case Number: 26-10"
    many = "109 W. Bangs Street; 280 Industrial Dr; 280 Industrial Drive; 9200 W Marlboro Rd"
    assert commercial.site_addresses(many, text) == "280 Industrial Drive"
    assert commercial.site_addresses(many, "no address in the item") == "109 W. Bangs Street; 280 Industrial Drive"
    assert commercial.site_addresses(None, text) is None


def test_commercial_labels_and_categories():
    b, cat = commercial.business, commercial.category
    hd = ("A petition filed by Home Depot USA Inc., owner, requesting a special use permit for vehicle and equipment "
          "rental per Section 13.3 and Section 18.3 of the Zoning Code, to be located at 493 N. Milwaukee Avenue.")
    assert b(hd) == "Home Depot USA Inc. — vehicle and equipment rental" and cat(hd) == "Retail"
    golf = ("Request for Approval of a Class B Special Use to Permit the Establishment of a Self-Improvement Facility and "
            "Approval of an Overnight Business in Deerfield Square at 710 Robert York Avenue, Unit C - Back Nine Golf "
            "and CRM Properties – THIS")
    assert b(golf) == "Back Nine Golf and CRM Properties — self-improvement facility" and cat(golf) == "Fitness"
    wash = commercial.ocr_fix("APPROVAL FOR A CAR WASH, EL CAR WA SH, TO BE LOCATED AT 1750 N. MILWAUKEE AVENUE")
    assert b(wash) == "El Car Wash — car wash"
    assert b("Conditional Use Permit for Automobile Washing at 650 W Liberty Street ... request from the owner and "
             "operator of Bubbles and Suds to authorize car washing") == "Bubbles and Suds — automobile washing"
    assert b("Consideration of an Amendment to the Planned Development Final Plan for the Carriage Hill Park Planned "
             "Development – 1480 N. Milwaukee Avenue") == "Carriage Hill Park Planned Development — final plan amendment"
    assert b("Public Hearing PUD-2026-00105 for an Amendment to a Special Use Permit in the Nature of a Planned "
             "Development, Sign Package Amendment, and Design Review (2000-2060 Skokie Valley Rd.)") == \
        "Planned development amendment, sign package and design review"
    assert b("Deerfield Commons: 710 Deerfield Road & 800 Waukegan Road - Facade Revision") == \
        "Deerfield Commons — façade revision"
    assert b("A petition filed by Centennial Real Estate, property owner, to revise the Hawthorn Mall PUD to facilitate "
             "the construction of a drive-through restaurant, Dutch") == "Centennial Real Estate — drive-through restaurant"
    assert b("a request for a special use to allow a motor vehicle body shop to be located on the lower level") == \
        "Motor vehicle body shop"
