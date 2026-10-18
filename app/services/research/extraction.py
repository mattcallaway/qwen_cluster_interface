"""
Document Extraction & Text Processing.

Extracts structured research content from HTML, PDF, plain text, and JSON documents:
- Document title, author, publication date, modified date
- Hierarchical headings (H1-H4)
- Clean, de-duplicated paragraph body text (stripped of nav, ads, headers, footers)
- Structured tables (converted to markdown/list representation)
- Outbound reference links
- PDF text extraction with page tracking via pypdf
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment

from app.services.research.cache import compute_content_hash

logger = logging.getLogger("qwen_collective.research.extraction")

# Elements and classes typical of navigation, ads, footers, popups
UNWANTED_TAGS = {"script", "style", "noscript", "iframe", "svg", "nav", "footer", "header", "aside"}
UNWANTED_SELECTORS = [
    ".nav", ".navbar", ".menu", ".header", ".footer", ".sidebar",
    ".advertisement", ".ad", ".ad-container", ".social-share",
    ".cookie-banner", ".popup", ".modal", "#comments", ".comments",
]

DATE_PATTERNS = [
    r"(\d{4}[-/]\d{2}[-/]\d{2})",
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4})",
    r"(\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4})",
]


@dataclass
class ExtractedDocument:
    """Standardized representation of extracted document content and metadata."""
    title: str
    author: Optional[str] = None
    publisher: Optional[str] = None
    publication_date: Optional[str] = None
    modified_date: Optional[str] = None
    canonical_url: Optional[str] = None
    clean_text: str = ""
    headings: List[Dict[str, str]] = field(default_factory=list)
    tables: List[List[List[str]]] = field(default_factory=list)
    outbound_urls: List[str] = field(default_factory=list)
    word_count: int = 0
    content_hash: str = ""
    content_type: str = "text/html"
    is_spa_placeholder: bool = False

    @property
    def text(self) -> str:
        return self.clean_text

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "publisher": self.publisher,
            "publication_date": self.publication_date,
            "modified_date": self.modified_date,
            "canonical_url": self.canonical_url,
            "word_count": self.word_count,
            "content_type": self.content_type,
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DocumentExtractor:
    """Extracts text, metadata, tables, and links from web documents."""

    def extract(self, content: bytes | str, content_type: str, base_url: str = "") -> ExtractedDocument:
        """Main extraction entrypoint based on MIME content type."""
        c_type = (content_type or "text/html").lower()

        if "pdf" in c_type or (isinstance(content, bytes) and content.startswith(b"%PDF-")):
            return self.extract_pdf(content, base_url)
        elif "json" in c_type:
            return self.extract_json(content, base_url)
        elif "plain" in c_type:
            return self.extract_plain_text(content, base_url)
        else:
            return self.extract_html(content, base_url)

    def extract_html(self, content: bytes | str, base_url: str = "") -> ExtractedDocument:
        """Parse and extract clean readable text and metadata from HTML."""
        if isinstance(content, bytes):
            # Decode with fallback
            try:
                html_text = content.decode("utf-8")
            except UnicodeDecodeError:
                html_text = content.decode("latin-1", errors="replace")
        else:
            html_text = content

        content_hash = compute_content_hash(html_text)
        soup = BeautifulSoup(html_text, "html.parser")

        # 1. Metadata extraction
        title = ""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

        og_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "twitter:title"})
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()

        author = None
        author_meta = (
            soup.find("meta", attrs={"name": "author"}) or
            soup.find("meta", property="article:author") or
            soup.find("meta", attrs={"name": "twitter:creator"})
        )
        if author_meta and author_meta.get("content"):
            author = author_meta["content"].strip()

        publisher = None
        pub_meta = soup.find("meta", property="og:site_name") or soup.find("meta", attrs={"name": "publisher"})
        if pub_meta and pub_meta.get("content"):
            publisher = pub_meta["content"].strip()
        elif base_url:
            publisher = urlparse(base_url).hostname

        pub_date = None
        date_meta = (
            soup.find("meta", property="article:published_time") or
            soup.find("meta", attrs={"name": "date"}) or
            soup.find("meta", attrs={"name": "pubdate"})
        )
        if date_meta and date_meta.get("content"):
            pub_date = date_meta["content"].strip()[:10]

        mod_date = None
        mod_meta = soup.find("meta", property="article:modified_time") or soup.find("meta", attrs={"name": "last-modified"})
        if mod_meta and mod_meta.get("content"):
            mod_date = mod_meta["content"].strip()[:10]

        canonical = base_url
        canon_tag = soup.find("link", rel="canonical")
        if canon_tag and canon_tag.get("href"):
            canonical = urljoin(base_url, canon_tag["href"].strip())

        # 2. Extract Headings before stripping
        headings: List[Dict[str, str]] = []
        for h in soup.find_all(["h1", "h2", "h3", "h4"]):
            h_text = h.get_text(strip=True)
            if h_text and len(h_text) > 2:
                headings.append({"level": h.name, "text": h_text})

        # 3. Extract Tables
        tables: List[List[List[str]]] = []
        for tbl in soup.find_all("table"):
            rows: List[List[str]] = []
            for tr in tbl.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(cells)
            if rows:
                tables.append(rows)

        # 4. Outbound URLs
        outbound: List[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("http://", "https://")):
                if href not in outbound:
                    outbound.append(href)
            elif href.startswith("/") and base_url:
                full_u = urljoin(base_url, href)
                if full_u not in outbound:
                    outbound.append(full_u)

        # 5. Clean DOM: remove scripts, styles, comments, unwanted tags & selectors
        for element in soup.find_all(string=lambda s: isinstance(s, Comment)):
            element.extract()

        for tag in UNWANTED_TAGS:
            for s in soup.find_all(tag):
                s.decompose()

        for sel in UNWANTED_SELECTORS:
            for s in soup.select(sel):
                s.decompose()

        # Find main article or content body container if available
        body_container = soup.find("article") or soup.find("main") or soup.find(id=re.compile(r"content|article|main", re.I)) or soup.body or soup

        # Extract text paragraphs
        paragraphs: List[str] = []
        for p in body_container.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote"]):
            p_text = p.get_text(separator=" ", strip=True)
            if len(p_text) > 5:
                # Deduplicate repeated phrases or navigational crumbs
                if not paragraphs or paragraphs[-1] != p_text:
                    paragraphs.append(p_text)

        clean_text = "\n\n".join(paragraphs)
        if not clean_text:
            clean_text = body_container.get_text(separator="\n", strip=True)

        word_count = len(clean_text.split())
        is_spa = word_count < 30 and any(spa in html_text.lower() for spa in ["id=\"root\"", "id=\"app\"", "id=\"__next\"", "noscript", "enable javascript"])

        return ExtractedDocument(
            title=title or "Untitled Document",
            author=author,
            publisher=publisher,
            publication_date=pub_date,
            modified_date=mod_date,
            canonical_url=canonical,
            clean_text=clean_text,
            headings=headings,
            tables=tables,
            outbound_urls=outbound[:50],
            word_count=word_count,
            content_hash=content_hash,
            content_type="text/html",
            is_spa_placeholder=is_spa,
        )

    def extract_pdf(self, content: bytes | str, base_url: str = "") -> ExtractedDocument:
        """Extract text and metadata from PDF bytes using pypdf."""
        if isinstance(content, str):
            content = content.encode("utf-8", errors="replace")

        content_hash = compute_content_hash(content)
        title = "PDF Document"
        author = None
        pub_date = None
        text_pages: List[str] = []

        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            meta = reader.metadata or {}
            if meta.title:
                title = str(meta.title).strip()
            if meta.author:
                author = str(meta.author).strip()
            if meta.creation_date:
                pub_date = str(meta.creation_date)[:10]

            for idx, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_pages.append(f"[Page {idx + 1}]\n" + page_text.strip())

        except Exception as e:
            logger.warning("PDF extraction error: %s", e)
            text_pages = [f"Error extracting PDF: {e}"]

        clean_text = "\n\n".join(text_pages)
        return ExtractedDocument(
            title=title,
            author=author,
            publisher=urlparse(base_url).hostname if base_url else "PDF Document",
            publication_date=pub_date,
            canonical_url=base_url,
            clean_text=clean_text,
            word_count=len(clean_text.split()),
            content_hash=content_hash,
            content_type="application/pdf",
        )

    def extract_plain_text(self, content: bytes | str, base_url: str = "") -> ExtractedDocument:
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
        return ExtractedDocument(
            title=base_url or "Plain Text Document",
            canonical_url=base_url,
            clean_text=text.strip(),
            word_count=len(text.split()),
            content_hash=compute_content_hash(text),
            content_type="text/plain",
        )

    def extract_json(self, content: bytes | str, base_url: str = "") -> ExtractedDocument:
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
        try:
            parsed = json.loads(text)
            pretty = json.dumps(parsed, indent=2)
        except Exception:
            pretty = text

        return ExtractedDocument(
            title=base_url or "JSON Data",
            canonical_url=base_url,
            clean_text=pretty,
            word_count=len(pretty.split()),
            content_hash=compute_content_hash(text),
            content_type="application/json",
        )
