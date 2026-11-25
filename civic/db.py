"""SQLite storage. One file, committed to the repo by the nightly job.

Full document text is NOT stored in the database (it would blow past GitHub's
100 MB file limit). It lives in data/text/<town>/<sha>.txt.gz; the database keeps
metadata, parsed agenda items, tags and news.
"""
from __future__ import annotations

import gzip
import hashlib
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "civic.db"
TEXT_DIR = DATA / "text"

SCHEMA = """
CREATE TABLE IF NOT EXISTS towns (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT,
  url TEXT,
  platform TEXT,            -- detected or configured
  status TEXT,              -- ok | unreachable | no_documents_found
  last_discovered TEXT,
  last_collected TEXT,
  note TEXT
);

-- one row per document link found (agenda, minutes, packet, notice, ordinance...)
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  town TEXT NOT NULL REFERENCES towns(slug),
  url TEXT NOT NULL UNIQUE,
  title TEXT,
  body TEXT,                -- Village Board, Plan Commission, ZBA, ...
  doc_type TEXT,            -- agenda | minutes | packet | notice | ordinance | resolution | other
  meeting_date TEXT,        -- ISO date if known
  source TEXT,              -- adapter that found it
  found_on TEXT,            -- page where the link was found
  first_seen TEXT NOT NULL,
  fetched_at TEXT,
  fetch_status TEXT,        -- pending | ok | error:<msg> | skipped:<why>
  content_type TEXT,
  sha256 TEXT,
  n_chars INTEGER,
  text_path TEXT
);
CREATE INDEX IF NOT EXISTS ix_docs_town_date ON documents(town, meeting_date);
CREATE INDEX IF NOT EXISTS ix_docs_status ON documents(fetch_status);

-- agenda/minutes items split out of document text
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  town TEXT NOT NULL,
  meeting_date TEXT,
  body TEXT,
  seq INTEGER,
  number TEXT,              -- "8.1", "A", "IV" ...
  text TEXT NOT NULL,
  tags TEXT,                -- comma list: rezoning,special_use,data_center,...
  action TEXT,              -- approved | denied | continued | tabled | withdrawn | discussed | NULL
  vote TEXT,                -- "4-2" etc when stated
  addresses TEXT,           -- addresses mentioned
  pins TEXT                 -- Lake County PINs mentioned
);
CREATE INDEX IF NOT EXISTS ix_items_town_date ON items(town, meeting_date);
CREATE INDEX IF NOT EXISTS ix_items_tags ON items(tags);

CREATE TABLE IF NOT EXISTS news (
  id INTEGER PRIMARY KEY,
  town TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  title TEXT,
  source TEXT,              -- publisher
  feed TEXT,                -- google_news | village_rss
  published TEXT,
  first_seen TEXT NOT NULL,
  tags TEXT
);
CREATE INDEX IF NOT EXISTS ix_news_town_pub ON news(town, published);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  started TEXT, finished TEXT, command TEXT, summary TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def save_text(town: str, content: bytes, text: str, text_dir: Path | None = None) -> tuple[str, str]:
    """Write gzipped text; return (sha256 of original bytes, relative path)."""
    text_dir = text_dir or TEXT_DIR
    sha = hashlib.sha256(content).hexdigest()
    rel = Path(town) / f"{sha[:16]}.txt.gz"
    out = text_dir / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            fh.write(text)
    return sha, str(Path("text") / rel)


def load_text(rel_path: str, data_dir: Path | None = None) -> str:
    data_dir = data_dir or DATA
    with gzip.open(data_dir / rel_path, "rt", encoding="utf-8") as fh:
        return fh.read()
