# Lake County civic leads

Village boards, plan commissions and zoning boards decide what gets built months before a
permit is pulled or a contractor is called. This code reads the public agendas and minutes of
every municipality in Lake County, IL (plus the county itself) every night and turns them into
**a ranked weekly lead list for local contractors**.

**Project page:** https://aleksandraa-max.github.io/lake-county-civic-leads/  
**Sample report:** [`sample/commercial-sample.md`](sample/commercial-sample.md)

This repository holds the code, configuration and tests only. The collected archive and the
weekly reports are kept private (see [Privacy](#privacy)).

Built with Claude Code.

## What it produces

- **Commercial build-out leads.** Private commercial projects a general contractor can win
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

1. **Discover** each town's website and agenda platform (CivicPlus, Revize, WordPress, Legistar,
   CivicClerk, Granicus…).
2. **Collect** agenda, minutes and packet links; **extract** text from the PDFs and pages.
3. **Classify** agenda items (rezoning, special use, variance, planned development, liquor
   license, car wash…) and read the decision and vote from minutes.
4. **Rank** commercial leads A/B/C and write the reports as Markdown, HTML and TSV.

It runs on a nightly GitHub Actions schedule with no servers. Tests run before every collection,
and a failed test marks the run red.

## Privacy

Agendas name homeowners who apply for a variance. The reports withhold names that look like
private people ("Petitioner …", "Appellant …", "filed by …, owners", including middle initials,
couples and "as Trustee") and the appellant's home address, and the commercial report drops cases
about private homes. Company names and official decisions stay.

The raw documents are not redacted, so the collected archive is not published here. Names and
addresses in the tests are made up.

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
