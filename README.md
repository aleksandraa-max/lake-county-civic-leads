# Lake County civic leads

**A weekly lead list for local contractors, built from public records with AI.**

Village boards, plan commissions and zoning boards decide what gets built months before a
permit is pulled or a contractor is called. Every night this code reads the agendas and minutes
those boards post (53 Lake County, Illinois governments tracked, 36 of them collected nightly),
and every Monday it produces a short, ranked list of businesses that are about to need a
build-out.

**Project page:** https://aleksandraa-max.github.io/lake-county-civic-leads/  
**Sample report:** [`sample/commercial-sample.md`](sample/commercial-sample.md)

This repository holds the code, configuration and tests only. The collected archive and the
weekly reports are kept private (see [What's public and what's private](#whats-public-and-whats-private)).

Built with Claude Code. The project page explains how it was built with AI and how the same
pattern works for other small businesses.

## What it produces

- **Commercial build-out leads (every Monday).** Private commercial projects a general contractor can win
  (restaurants, medical, daycare, fitness, retail and industrial build-outs), ranked:
  - **A, act this week**: approved, or a new liquor license or new owner, in an existing building
    or suite. These are interior build-outs, usually under $2M, often for owner-operators who hire
    local contractors.
  - **B, build the relationship**: pending cases, multi-tenant centers leasing up (sign
    variations, façades), spec industrial.
  - **C, watch**: ground-up and chain projects that are probably over $2M; the tenants inside are
    the opportunity.
  - Left out: houses, fences and garages, public works, budgets, license renewals and other
    administrative items.
- **Builder report.** Teardowns and new homes, zoning cases, bid results and rule changes that
  affect builders, for a chosen set of towns.

Every entry links to the village's own document. Each case appears once, even when it shows up in
the agenda, the packet and the minutes; the copy that records the decision wins. The rules are
deliberately plain and readable: see `civic/commercial.py` and `civic/report.py`.

## How it works

1. **Discover** each town's agenda pages and platform (CivicPlus, Revize, WordPress, Legistar,
   CivicClerk, Granicus…).
2. **Collect** new agendas, minutes and packets.
3. **Extract** the text from PDFs and pages.
4. **Classify**: split documents into items, tag them (rezoning, special use, variance, planned
   development, liquor license, car wash…) and record decisions and votes from minutes.
5. **Digest**: write town digests and the weekly lead reports (Markdown, HTML, and a TSV that
   drops straight into a lead spreadsheet), with commercial leads ranked A/B/C.

It runs nightly on GitHub Actions' free scheduler with no servers. Tests run before every
collection, and a failed test marks the run red. Some village sites block automated requests,
which is why fewer towns are collected than tracked.

## What's public and what's private

| Public (this repo) | Private |
| --- | --- |
| The code, the ranking rules, the tests, a sample report and the project page | The collected archive (the text of every agenda, minutes and packet, and the database built from them) and the full weekly reports |

**Why the code is public.** It is the part worth showing, and it contains nothing sensitive.
Anyone can read exactly how a lead is picked and ranked.

**Why the archive stays private.** Every document comes from a public village website, but
agendas, packets and minutes also name residents: homeowners who apply for a variance, people
who speak at meetings, and sometimes their home addresses, phone numbers and emails. Each one is
public on its own, buried in a PDF on one village's site. Gathered into one searchable
collection, they would make it easy to look people up, which the original postings don't.

**What the reports show.** The leads are about businesses, so business names, business
addresses and official decisions stay. Names that look like private people ("Petitioner …",
"Appellant …", "filed by …, owners", including middle initials, couples and "as Trustee") and
the appellant's home address are withheld, and cases about private homes are dropped from the
commercial report. That filtering is rule-based and good but not perfect, which is one more
reason the raw text is not published. Names and addresses in the tests are made up.

## Run it yourself

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q tests                       # offline tests, no network
export CONTACT_EMAIL=you@example.com            # goes in the crawler's User-Agent
python -m civic run --towns deerfield --max-minutes 20
python -m civic report --towns deerfield
python -m civic search "car wash"
```

Towns live in `config/towns.yaml`: homepage, optional `seeds:` (agenda pages to start from) and
notes. Output goes to `data/`, which git ignores.

## Ground rules for the crawler

- One request per second per site, robots.txt honored, files over 60 MB skipped.
- It identifies itself with a contact email, so village IT staff can reach the operator.
- Public records only.

## Known limits

- Some village sites block requests from cloud servers; others load agendas with scripts and need
  their own adapters.
- Scanned PDFs have no text until OCR is added.
- Labels come from agenda wording, so some leads still need a look at the packet.
