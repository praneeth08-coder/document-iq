"""DocumentIQ 2.0 - Document Ingestion & Text Extraction.

Robust extraction for PDF, DOCX, DOC and TXT with structure, pagination,
and character normalization.
"""
import io
import re
import zipfile

MAX_PAGES = 300
MAX_CLAUSES = 3500
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SUPPORTED_MESSAGE = "Unsupported file type. Please upload a PDF, DOC, DOCX or TXT file."


class ExtractionError(Exception):
    """Raised when a document cannot be processed."""


def clean_text(text: str) -> str:
    """Normalize whitespace and control characters."""
    if not text:
        return ""
    text = text.replace("\u00a0", " ").replace("\u00ad", "").replace("\ufeff", "")
    return _CONTROL.sub(" ", text)


def detect_type(ext: str, data: bytes) -> str:
    """Detect real file type from content signature, falling back to extension."""
    ext = (ext or "").lower().lstrip(".")
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "docx"
    if data.startswith(OLE_MAGIC):
        return "doc"
    return ext


def extract_pages(ext: str, data: bytes):
    """Extract pages as list of (page_num_or_None, clean_text)."""
    kind = detect_type(ext, data)
    if kind == "pdf":
        pages = _extract_pdf(data)
    elif kind == "docx":
        pages = _extract_docx(data)
    elif kind == "doc":
        pages = _extract_doc(data)
    elif kind == "txt":
        pages = _extract_txt(data)
    else:
        raise ExtractionError(SUPPORTED_MESSAGE)

    pages = [(n, clean_text(t)) for n, t in pages]
    total_len = sum(len(t.strip()) for _, t in pages)
    if total_len < 30:
        if kind == "pdf":
            raise ExtractionError(
                "No readable text found. This looks like a scanned or image-only PDF. "
                "Please upload a text-based PDF, DOC, DOCX or TXT."
            )
        raise ExtractionError("No readable text found in this file.")
    return pages


def _extract_pdf(data: bytes):
    if b"%PDF" not in data[:1024]:
        raise ExtractionError("This file is not a valid PDF.")
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise ExtractionError("The PDF could not be opened. The file may be damaged.")
    try:
        if doc.needs_pass:
            raise ExtractionError("This PDF is password protected. Remove password and try again.")
        if doc.page_count > MAX_PAGES:
            raise ExtractionError(f"PDF exceeds maximum page limit ({MAX_PAGES} pages).")
        pages = []
        for idx, page in enumerate(doc, start=1):
            blocks = page.get_text("blocks", sort=True)
            texts = [b[4] for b in blocks if b[6] == 0 and b[4].strip()]
            pages.append((idx, "\n\n".join(texts)))
        return pages
    finally:
        doc.close()


def _extract_docx(data: bytes):
    try:
        from docx import Document
    except ImportError:
        raise ExtractionError("python-docx is required to read DOCX files.")
    try:
        doc = Document(io.BytesIO(data))
    except (zipfile.BadZipFile, KeyError):
        raise ExtractionError("The Word document is damaged or not a valid DOCX file.")
    except Exception as exc:
        raise ExtractionError(f"Could not read DOCX: {exc}")

    parts = []
    for p in doc.paragraphs:
        t = p.text.strip()
        if t:
            parts.append(t)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return [(1, "\n\n".join(parts))]


def _extract_doc(data: bytes):
    # Basic binary string scan for legacy .doc
    text = data.decode("latin-1", errors="ignore")
    runs = re.findall(r"[\x20-\x7e\t\r\n]{5,}", text)
    filtered = [r.strip() for r in runs if len(r.strip()) > 10 and not r.startswith("<?xml")]
    return [(1, "\n\n".join(filtered))]


def _extract_txt(data: bytes):
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return [(1, data.decode(enc))]
        except UnicodeDecodeError:
            continue
    raise ExtractionError("Could not decode TXT file encoding.")
