"""Turn fetched files into plain text."""
from __future__ import annotations

import io
import re
import zipfile

from bs4 import BeautifulSoup


def pdf_text(content: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    parts = []
    for page in reader.pages[:400]:  # very long packets: first 400 pages
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page shouldn't kill the doc
            parts.append("")
    return "\n".join(parts)


def docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def html_text(content: bytes) -> str:
    soup = BeautifulSoup(content, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    return soup.get_text("\n", strip=True)


def to_text(content: bytes, content_type: str, url: str) -> tuple[str, str]:
    """Return (text, kind). kind in pdf|docx|html|unknown."""
    u = url.lower()
    if content[:5] == b"%PDF-" or "pdf" in content_type or u.endswith(".pdf"):
        return pdf_text(content), "pdf"
    if content[:2] == b"PK" and (u.endswith(".docx") or "word" in content_type):
        return docx_text(content), "docx"
    if "html" in content_type or content.lstrip()[:1] == b"<":
        return html_text(content), "html"
    return "", "unknown"
