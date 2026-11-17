"""Rule-based document analysis.

Text extraction, clause splitting, clause rules, obligation extraction and
version comparison. No machine learning and no external APIs: every result
can be traced back to a rule written in this file.
"""
import difflib
import io
import re
import zipfile

import semantic

MAX_PAGES = 300
MAX_CLAUSES = 3000
MAX_COMPARE_UNMATCHED = 400
SIMILARITY_THRESHOLD = 0.6

LEVELS = {"High": 3, "Medium": 2, "Low": 1}
LEVEL_NAMES = {3: "High", 2: "Medium", 1: "Low"}
CATEGORIES = ["Renewal", "Cancellation", "Refund", "Payment", "Privacy",
              "Liability", "Disputes", "Changes"]


class ExtractionError(Exception):
    """Raised with a user-friendly message when a file cannot be read."""


# --------------------------------------------------------------------------
# 1. Text extraction
# --------------------------------------------------------------------------
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(text):
    text = text.replace("\u00a0", " ").replace("\u00ad", "").replace("\ufeff", "")
    return _CONTROL.sub(" ", text)


OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
SUPPORTED_MESSAGE = "Unsupported file type. Please upload a PDF, DOC, DOCX or TXT file."


def detect_type(ext, data):
    """Decide the real format from the file contents, falling back to the extension.
    This lets a Word file saved with the wrong extension (or a PDF named .txt) still open."""
    ext = (ext or "").lower().lstrip(".")
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "docx"
    if data.startswith(OLE_MAGIC):
        return "doc"
    return ext


def extract_pages(ext, data):
    """Return a list of (page_number_or_None, text) for pdf/doc/docx/txt bytes."""
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
    pages = [(n, _clean(t)) for n, t in pages]
    total = sum(len(t.strip()) for _, t in pages)
    if total < 40:
        if kind == "pdf":
            raise ExtractionError(
                "No readable text was found. This looks like a scanned or image-only PDF, "
                "and OCR is not supported. Upload a text-based PDF, DOC, DOCX or TXT instead.")
        raise ExtractionError("No readable text was found in this file.")
    return pages


def _extract_pdf(data):
    if b"%PDF" not in data[:1024]:
        raise ExtractionError("This file is not a valid PDF.")
    try:
        import pymupdf as fitz
    except ImportError:  # older PyMuPDF releases
        import fitz
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise ExtractionError("The PDF could not be opened. The file may be damaged.")
    try:
        if doc.needs_pass:
            raise ExtractionError("This PDF is password protected. Remove the password and try again.")
        if doc.page_count > MAX_PAGES:
            raise ExtractionError(f"This PDF has more than {MAX_PAGES} pages, which is above the limit.")
        pages = []
        for number, page in enumerate(doc, start=1):
            blocks = page.get_text("blocks", sort=True)
            texts = [b[4] for b in blocks if b[6] == 0 and b[4].strip()]
            pages.append((number, "\n\n".join(texts)))
        return pages
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("Text could not be extracted from this PDF.")
    finally:
        doc.close()


def _extract_docx(data):
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ExtractionError("This file is not a valid DOCX document.")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if "word/document.xml" not in zf.namelist():
            raise ExtractionError("This is a ZIP-based file but not a Word document. "
                                  "Spreadsheets and presentations are not supported.")
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc = Document(io.BytesIO(data))
        lines = []
        for child in doc.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                lines.append(Paragraph(child, doc).text)
            elif tag == "tbl":
                for row in Table(child, doc).rows:
                    seen, cells = set(), []
                    for cell in row.cells:
                        if id(cell._tc) in seen:
                            continue
                        seen.add(id(cell._tc))
                        if cell.text.strip():
                            cells.append(cell.text.strip())
                    if cells:
                        lines.append(" | ".join(cells))
        return [(None, "\n\n".join(lines))]
    except Exception:
        raise ExtractionError("The DOCX file could not be read. It may be damaged or password protected.")


def _doc_text(raw):
    """Turn raw Word text (with control characters) into plain paragraphs."""
    out, fields = [], []
    for ch in raw:
        if ch == "\x13":            # field begins: the code part is skipped
            fields.append(True)
            continue
        if ch == "\x14":            # separator: the visible result follows
            if fields:
                fields[-1] = False
            continue
        if ch == "\x15":            # field ends
            if fields:
                fields.pop()
            continue
        if any(fields):
            continue
        if ch in "\r\x07\x0c":     # paragraph, table cell or page break
            out.append("\n\n")
        elif ch == "\x0b":          # soft line break
            out.append("\n")
        elif ch == "\x1e":          # non-breaking hyphen
            out.append("-")
        elif ch in "\t\n" or ch >= " ":
            out.append(ch)
    return re.sub(r"\n{3,}", "\n\n", "".join(out))


def _extract_doc(data):
    """Read the main text of a Word 97-2003 (.doc) file using the olefile package."""
    import struct
    try:
        import olefile
    except ImportError:
        raise ExtractionError("Reading .doc files needs the olefile package. Run: pip install olefile")
    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception:
        raise ExtractionError("This .doc file could not be opened. It may be damaged.")
    try:
        if not ole.exists("WordDocument"):
            raise ExtractionError("This is an Office file but not a Word document.")
        word = ole.openstream("WordDocument").read()
        if len(word) < 0x1AA:
            raise ExtractionError("This .doc file is damaged or incomplete.")
        n_fib = struct.unpack_from("<H", word, 0x02)[0]
        flags = struct.unpack_from("<H", word, 0x0A)[0]
        if n_fib < 0x00C1:
            raise ExtractionError("This is a very old Word format (Word 6 or 95). "
                                  "Open it in Word and save it as DOCX or PDF first.")
        if flags & 0x0100:
            raise ExtractionError("This document is password protected. Remove the password and try again.")
        table_name = "1Table" if flags & 0x0200 else "0Table"
        if not ole.exists(table_name):
            raise ExtractionError("This .doc file is damaged or incomplete.")
        table = ole.openstream(table_name).read()
        ccp_text = struct.unpack_from("<i", word, 0x4C)[0]
        fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x1A2)
        clx = table[fc_clx:fc_clx + lcb_clx]
        i = 0
        while i + 3 <= len(clx) and clx[i] == 0x01:      # skip property modifiers
            i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
        if i + 5 > len(clx) or clx[i] != 0x02 or ccp_text < 0:
            raise ExtractionError("This .doc file is damaged or uses an unsupported layout.")
        lcb = struct.unpack_from("<I", clx, i + 1)[0]
        plc = clx[i + 5:i + 5 + lcb]
        n = (lcb - 4) // 12
        if n < 1 or 4 * (n + 1) + 8 * n > len(plc):
            raise ExtractionError("This .doc file is damaged or uses an unsupported layout.")
        cps = struct.unpack_from("<%dI" % (n + 1), plc, 0)
        parts = []
        for k in range(n):
            start, end = cps[k], min(cps[k + 1], ccp_text)
            if end <= start:
                continue
            fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)[0]
            length = end - start
            if fc & 0x40000000:                           # 8-bit text
                off = (fc & 0x3FFFFFFF) // 2
                parts.append(word[off:off + length].decode("cp1252", errors="replace"))
            else:                                         # UTF-16 text
                parts.append(word[fc:fc + 2 * length].decode("utf-16-le", errors="replace"))
        return [(None, _doc_text("".join(parts)))]
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("The .doc file could not be read. It may be damaged.")
    finally:
        ole.close()


def _extract_txt(data):
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        text = data.decode("utf-16", errors="replace")
    elif b"\x00" in data[:4096]:
        raise ExtractionError("This does not look like a plain text file.")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    return [(None, text)]


# --------------------------------------------------------------------------
# 2. Clause splitting
# --------------------------------------------------------------------------
_START_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)+\.?|\d+[.)]|\(\s*(?:\d{1,2}|[A-Za-z]{1,3})\s*\)|[A-Za-z][.)]"
    r"|[-\u2022*\u25aa\u25cf\u2013\u2014]|(?:Section|Article|Clause)\s+\d+[.:)]?)(?=\s)")
_ABBR = re.compile(
    r"\b(?:Rs|Mr|Mrs|Ms|Dr|Prof|Inc|Ltd|Co|Corp|No|Nos|vs|etc|Sec|Art|approx|St|Jr|Sr|Pvt|"
    r"Govt|Dept|Fig|e\.g|i\.e|U\.S|U\.K|a\.m|p\.m)\.", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u201c\"(\[])")


def _join_lines(lines):
    out = lines[0]
    for line in lines[1:]:
        if re.search(r"[A-Za-z]-$", out) and line[:1].islower():
            out = out[:-1] + line
        else:
            out += " " + line
    return re.sub(r"\s+", " ", out).strip()


def _heading_line(line, nxt):
    """A short numbered, ALL-CAPS or colon-ended line is a heading, unless the next
    line starts lowercase (which means the line was only wrapped)."""
    if not (_START_RE.match(line) or line.isupper() or line.endswith(":")):
        return False
    _, body = _split_prefix(line)
    if not body or not _is_heading(body):
        return False
    return not (nxt and nxt[:1].islower())


def _blocks(text):
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    blocks, cur = [], []
    for idx, line in enumerate(lines):
        if not line:
            if cur:
                blocks.append(cur)
                cur = []
            continue
        nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
        if _heading_line(line, nxt):
            if cur:
                blocks.append(cur)
            blocks.append([line])
            cur = []
            continue
        if cur and _START_RE.match(line):
            blocks.append(cur)
            cur = []
        cur.append(line)
    if cur:
        blocks.append(cur)
    return [_join_lines(b) for b in blocks]


def _split_prefix(block):
    m = _START_RE.match(block)
    if not m:
        return "", block
    token = m.group(0).strip()
    ref = token if re.search(r"[A-Za-z0-9]", token) else ""
    if len(ref) > 1 and ref.endswith("."):
        ref = ref[:-1]
    return ref, block[m.end():].strip()


def _is_heading(text):
    t = text.strip()
    if t.endswith(":"):
        t = t[:-1].strip()
    words = t.split()
    if not words or len(words) > 8:
        return False
    if t[-1] in ".!?;,":
        return False
    return t[0].isupper() or t[0].isdigit()


def _sentences(text):
    protected = _ABBR.sub(lambda m: m.group(0)[:-1] + "\x00", text)
    parts = _SENT_SPLIT.split(protected)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


def build_clauses(pages):
    """Split extracted pages into clause dicts. Returns (clauses, truncated)."""
    clauses, section, truncated = [], "", False
    for page, text in pages:
        for block in _blocks(text):
            ref, body = _split_prefix(block)
            if not body:
                continue
            if _is_heading(body):
                section = (ref + ". " if ref and ref[0].isdigit() else "") + body.rstrip(":").strip()
                top = [r for r in analyze_clause(body) if r["level"] != "Low"]
                if not (top and len(body.split()) >= 2):
                    continue  # a plain heading, not a clause such as "No refunds"
            for sentence in _sentences(body):
                n_words = len(sentence.split())
                if n_words < 2 or (n_words < 3 and not any(
                        r["level"] != "Low" for r in analyze_clause(sentence))):
                    continue  # too short to be a meaningful clause (but keep "No refunds.")
                if len(clauses) >= MAX_CLAUSES:
                    return clauses, True
                clauses.append({"id": len(clauses) + 1, "page": page, "ref": ref,
                                "section": section, "text": sentence})
    return clauses, truncated


# --------------------------------------------------------------------------
# 3. Rules
# --------------------------------------------------------------------------
_CUR = r"(?:Rs\.?|INR|\u20b9|\$|USD|EUR|\u20ac|\u00a3|GBP)"
_AMOUNT = (r"(?<![A-Za-z0-9])" + _CUR + r"\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:lakhs?|crores?|million|thousand|k)\b)?"
           r"|(?<![\w.,])\d[\d,]*(?:\.\d+)?\s?(?:rupees|dollars|euros|USD|INR|EUR|GBP)\b")
AMOUNT_RE = re.compile(_AMOUNT, re.I)
PERCENT_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?(?:%|percent\b|per\s+cent\b)", re.I)

RULES = []


def _add(rule_id, category, level, title, plain, patterns, neg=False):
    RULES.append({"id": rule_id, "category": category, "level": level, "title": title,
                  "plain": plain, "neg": neg,
                  "patterns": [re.compile(p, re.I) for p in patterns]})


# Rules are ordered by severity inside each category. Only the first rule that
# matches is reported per category for each clause.
_add("renew_auto", "Renewal", "High", "Automatic renewal",
     "The agreement continues on its own for another term unless you act in time. "
     "Find the deadline for stopping it and put it in your calendar.",
     [r"\bauto[- ]?renew\w*", r"\brenew\w*\s+automatically\b",
      r"\bautomatic(?:ally)?\s+(?:renew|extend|continu)\w*", r"\bautomatic\s+renewal",
      r"\b(?:renew|extend)\w*\s+(?:for\s+)?(?:successive|consecutive)\s+\w+", r"\bevergreen\b"],
     neg=True)
_add("renew_price", "Renewal", "Medium", "Price may change on renewal",
     "The cost can go up when the agreement renews. Check how much notice you get first.",
     [r"\b(?:renewal|renewed)\s+(?:fee|price|rate|charge|rent|term)s?\b[^.]{0,40}\b(?:increase|higher|change|adjust)\w*",
      r"\b(?:rent|price|fee|rate|charge)s?\b[^.]{0,40}\b(?:increase|adjust)\w*\s+(?:on|upon|at|during)\s+renewal",
      r"\b(?:increase|raise|adjust)\w*\s+(?:the\s+)?(?:rent|price|fee|rate|charge)s?\s+(?:on|upon|at)\s+renewal"],
     neg=True)
_add("renew_rolling", "Renewal", "Medium", "Rolling term",
     "The agreement keeps running until someone ends it. Know how and when you can end it.",
     [r"\bmonth[- ]to[- ]month\b", r"\bcontinues?\s+(?:in\s+effect\s+)?until\s+(?:terminated|cancell?ed)"])
_add("renew_mention", "Renewal", "Low", "Renewal mentioned",
     "Renewal is mentioned. Check how and when the agreement renews.",
     [r"\brenew(?:al|ed|s|ing)?\b"])

_add("cancel_fee", "Cancellation", "High", "Fee for ending early",
     "You may have to pay money if you end the agreement early. Work out the total cost before you commit.",
     [r"\b(?:early\s+)?(?:termination|cancell?ation|exit|withdrawal)\s+(?:fee|charge|penalt(?:y|ies))s?\b",
      r"\b(?:fee|charge|penalt(?:y|ies))s?\s+(?:for|upon|on)\s+(?:early\s+)?(?:termination|cancell?ation|terminating|cancell?ing)\b",
      r"\bliquidated\s+damages\b",
      r"\bpay\w*\s+(?:the\s+)?(?:remaining|balance|rest)\b[^.]{0,40}\b(?:term|contract|rent|payments?)\b"],
     neg=True)
_add("cancel_blocked", "Cancellation", "High", "Cancellation is restricted",
     "The wording says the agreement cannot be cancelled or ended, or only in limited cases. "
     "Check whether you have any way out.",
     [r"\bnon[- ]?cancell?able\b",
      r"\b(?:cannot|can\s*not|may\s+not|shall\s+not|will\s+not)\s+(?:be\s+)?(?:cancell?ed|terminated)\b",
      r"\bno\s+(?:right\s+to\s+)?(?:cancell?ation|cancel|terminate)\b", r"\birrevocabl\w*"])
_add("cancel_provider", "Cancellation", "High", "Other party can end it freely",
     "The other party may end or suspend the agreement at its own choice, sometimes without notice.",
     [r"\b(?:terminate|suspend|cancel)\w*\b[^.]{0,80}\b(?:at\s+any\s+time|sole\s+discretion|without\s+(?:any\s+)?(?:prior\s+)?(?:notice|cause|reason|liability))\b",
      r"\bsole\s+discretion\b[^.]{0,60}\b(?:terminat|suspend|cancel)\w*"],
     neg=True)
_add("cancel_notice", "Cancellation", "Medium", "Notice is required",
     "A notice period applies. Missing the deadline or the required form can cost you money or extend the agreement.",
     [r"\b(?:written|prior|advance|\d+[- ]?day|thirty[- ]day)\s+notice\b",
      r"\bnotice\s+(?:period|of\s+(?:termination|cancell?ation|non[- ]?renewal))\b",
      r"\bgive\w*\s+(?:\w+\s+){0,3}notice\b",
      r"\bnotice\b[^.]{0,40}\b(?:cancel|terminat|non[- ]?renew|vacate)\w*",
      r"\b(?:cancel|terminat)\w*[^.]{0,60}\bnotice\b"])
_add("cancel_mention", "Cancellation", "Low", "Ending the agreement",
     "This describes how the agreement can end. Check the conditions.",
     [r"\b(?:cancel\w*|terminat\w*)\b"])

_add("refund_none", "Refund", "High", "No refund",
     "You may not get money back in this situation. Check when this applies before you pay.",
     [r"\bnon[- ]?refundable\b", r"\bno\s+refunds?\b",
      r"\b(?:not|never)\s+(?:be\s+)?(?:eligible\s+for\s+(?:a\s+|any\s+)?)?refund(?:ed|s)?\b",
      r"\bwithout\s+(?:any\s+)?refund\b", r"\bforfeit\w*\b",
      r"\bnot\s+(?:be\s+)?(?:returned|returnable)\b"])
_add("refund_limited", "Refund", "Medium", "Refund can be reduced",
     "Any refund may be reduced by deductions or depends on the other party's choice.",
     [r"\brefund\w*\b[^.]{0,50}\b(?:discretion|deduct\w*|less\s+(?:a|any|an)\b|processing\s+fee|administrative\s+fee)",
      r"\b(?:discretion|deduction)\b[^.]{0,50}\brefund\w*"])
_add("refund_mention", "Refund", "Low", "Refund mentioned",
     "Refunds are mentioned. Check the conditions and deadlines.",
     [r"\b(?:full|partial|pro[- ]?rat(?:a|ed)|prorated)\s+refund", r"\brefund\w*\b"])

_add("pay_late", "Payment", "Medium", "Extra charges for late payment",
     "Paying late can cost extra through fees, interest or penalties.",
     [r"\blate\s+(?:fee|payment|charge)s?\b", r"\binterest\s+(?:at|of|on|will|shall)\b",
      r"\b(?:penalt(?:y|ies)|surcharge)\b",
      r"\b(?:overdue|delinquent|past\s+due)\s+(?:amount|payment|balance|rent|invoice)s?\b"],
     neg=True)
_add("pay_deposit", "Payment", "Medium", "Deposit or advance payment",
     "Money is held or paid upfront. Check how much, when it is returned and what can be deducted.",
     [r"\b(?:security\s+)?deposit\b", r"\badvance\s+(?:payment|rent)\b"],
     neg=True)
_add("pay_change", "Payment", "Medium", "Price can change",
     "The amount you pay can be increased. Check how much notice you get and whether there is a cap.",
     [r"\b(?:fee|price|rate|rent|charge|cost)s?\b[^.]{0,50}\b(?:may|can|will|shall|reserve\w*)\s+(?:be\s+)?(?:increase|change|adjust|revis|rais)\w*",
      r"\b(?:increase|raise|adjust|revise)\w*\s+(?:the\s+)?(?:fee|price|rate|rent|charge|cost)s?\b"],
     neg=True)
_add("pay_duty", "Payment", "Low", "Payment obligation",
     "You are expected to pay. Check the amount, the due date and what happens if you miss it.",
     [r"\b(?:shall|must|agrees?\s+to|will|required\s+to|responsible\s+for|obligated\s+to)\s+(?:\w+\s+){0,2}pay\w*\b",
      r"\bpayable\b", r"\binvoice\w*\b", r"\bbill(?:ed|ing)?\b", _AMOUNT])

_add("privacy_share", "Privacy", "High", "Data sharing or sale",
     "Your personal information may be sold or passed to other companies. Check who they are and whether you can opt out.",
     [r"\b(?:sell|sells|sold|sale\s+of)\b[^.]{0,40}\b(?:data|information)\b",
      r"\b(?:share|shares|shared|sharing|disclose\w*|transfer\w*|provide\w*|give\w*)\b[^.]{0,60}\b(?:data|information|details)\b[^.]{0,60}\b(?:third[- ]part(?:y|ies)|affiliates?|partners?|advertis\w+|marketing|vendors?)\b",
      r"\b(?:third[- ]part(?:y|ies)|affiliates?|advertis\w+)\b[^.]{0,60}\b(?:access|receive|use)\w*\b[^.]{0,40}\b(?:data|information)\b"],
     neg=True)
_add("privacy_collect", "Privacy", "Medium", "Data collection",
     "Information about you is collected or kept. Check what is collected, why and for how long.",
     [r"\b(?:collect|collects|collected|collecting|store|stores|stored|retain\w*|track\w*|monitor\w*|record\w*|process\w*)\b[^.]{0,60}\b(?:personal|usage|location|device|browsing|your|user|customer)\b[^.]{0,25}\b(?:data|information|details|activity|behaviou?r)\b"],
     neg=True)
_add("privacy_mention", "Privacy", "Low", "Privacy or confidentiality",
     "This covers how information is handled. Read it to see what applies to you.",
     [r"\bprivacy\b", r"\bpersonal\s+(?:data|information)\b", r"\bconfidential\w*\b", r"\bdata\s+protection\b"])

_add("liab_limit", "Liability", "High", "Responsibility is limited",
     "The other party limits or excludes what it will cover if something goes wrong. You may bear the loss yourself.",
     [r"\b(?:not|no)\s+(?:be\s+)?(?:held\s+)?(?:liable|responsible|accountable)\b", r"\bno\s+liability\b",
      r"\b(?:disclaim\w*|exclud\w*|waive\w*|release\w*)\b[^.]{0,60}\b(?:liabilit\w*|warrant\w*|damages|claims?)\b",
      r"\b(?:liabilit\w*|damages)\b[^.]{0,60}\b(?:limited\s+to|shall\s+not\s+exceed|capped|not\s+exceed)\b",
      r"\bin\s+no\s+event\b", r"\bas[- ]is\b", r"\bwithout\s+(?:any\s+)?warrant\w*\b",
      r"\bat\s+(?:your|the\s+tenant'?s?|the\s+user'?s?|the\s+customer'?s?)\s+own\s+risk\b"])
_add("liab_indemnity", "Liability", "High", "You may have to cover the other party's losses",
     "An indemnity clause can make you pay for the other party's losses or legal costs.",
     [r"\bindemnif\w*", r"\bhold\w*\s+(?:\w+\s+){0,2}harmless\b",
      r"\bresponsible\s+for\s+(?:all|any)\s+(?:losses|costs|damages|claims|expenses)\b"])
_add("liab_mention", "Liability", "Medium", "Who is responsible",
     "This describes who is responsible if something goes wrong. Check whether it is balanced.",
     [r"\bliab(?:le|ility|ilities)\b", r"\bdamages\b", r"\bwarrant(?:y|ies)\b"])

_add("dispute", "Disputes", "High", "Limits on how disputes are handled",
     "Disputes may have to go to private arbitration or a set court, and some legal rights may be waived.",
     [r"\barbitrat\w*", r"\bclass[- ]action\b", r"\bwaive\w*\b[^.]{0,60}\b(?:jury|right\s+to\s+sue|court)\b",
      r"\bexclusive\s+jurisdiction\b"])

_add("change_unilateral", "Changes", "High", "Terms can change without your agreement",
     "The other party can change the terms or fees on its own, possibly without telling you first.",
     [r"\b(?:chang|modif|amend|updat|revis|alter|vary)\w*\b[^.]{0,80}\b(?:without\s+(?:any\s+)?(?:prior\s+)?(?:notice|consent)|sole\s+discretion|at\s+any\s+time)\b",
      r"\b(?:sole\s+discretion|at\s+any\s+time)\b[^.]{0,60}\b(?:chang|modif|amend|updat|revis|alter)\w*"],
     neg=True)
_add("change_right", "Changes", "Medium", "Right to change terms",
     "The other party keeps the right to update these terms. Check how you will be told.",
     [r"\b(?:reserves?\s+the\s+right|may|can|is\s+entitled)\s+(?:to\s+)?(?:change|modify|amend|update|revise|alter|vary)\w*\b[^.]{0,60}\b(?:terms|agreement|policy|policies|conditions|fees|prices|rates|rules)\b"],
     neg=True)

NEGATED_PLAIN = ("The sentence seems to say the opposite, using a word like \"not\" or \"no\" nearby. "
                 "That usually works in your favour, but read the full sentence to be sure.")

_NEG_RE = re.compile(r"\b(?:not|no|never|neither|nor|without|cannot)\b|n[\u2019']t\b", re.I)
_NEG_SKIP = re.compile(r"\b(?:no|not)\s+(?:later|earlier|less|more|fewer|greater|only)\b|\bnot\s+only\b", re.I)


def _is_negated(text, start):
    """Basic context rule: is there a negation just before the matched phrase?"""
    before = text[max(0, start - 45):start]
    cut = max(before.rfind(","), before.rfind(";"), before.rfind(":"), before.rfind("("))
    if cut != -1:
        before = before[cut + 1:]
    for sep in (" but ", " and ", " however ", " although ", " while ", " whereas "):
        i = before.lower().rfind(sep)
        if i != -1:
            before = before[i + len(sep):]
    before = _NEG_SKIP.sub(" ", before)
    return bool(_NEG_RE.search(before))


def _analyze_rules_only(text):
    """Keyword rules only (the original method). Kept for comparison and testing."""
    reasons, hit = [], set()
    for rule in RULES:
        if rule["category"] in hit:
            continue
        m = None
        for rx in rule["patterns"]:
            m = rx.search(text)
            if m:
                break
        if not m:
            continue
        negated = rule["neg"] and _is_negated(text, m.start())
        start, end = m.start(), m.end()
        if end - start > 70:
            end = start + 70
            while end < len(text) and not text[end].isspace():
                end += 1
        matched = re.sub(r"\s+", " ", m.group(0)).strip()
        if len(matched) > 90:
            matched = matched[:87].rstrip() + "..."
        reasons.append({
            "category": rule["category"],
            "level": "Low" if negated else rule["level"],
            "title": rule["title"],
            "matched": matched,
            "explanation": NEGATED_PLAIN if negated else rule["plain"],
            "negated": bool(negated),
            "span": [start, end],
        })
        hit.add(rule["category"])
    reasons.sort(key=lambda r: (-LEVELS[r["level"]], CATEGORIES.index(r["category"])))
    return reasons


# ---- Meaning and context layer -------------------------------------------
SEM_MIN = 0.38        # minimum whole-sentence similarity before a meaning match is considered
MIN_CONF = 0.40       # findings below this confidence are dropped
# Who must hold the power or burden for the clause to be a risk to the reader.
# "other": a risk when the other side holds it; favourable when the sentence is about "you".
# "you":   a risk when you carry it; favourable when the sentence is about the other side.
RISK_ACTOR = {"cancel_provider": "other", "pay_change": "other", "change_unilateral": "other",
              "change_right": "other", "privacy_share": "other", "privacy_collect": "other",
              "liab_limit": "other", "liab_indemnity": "you", "liab_mention": "you"}
ACTOR_EXEMPT = {"liab_limit": {"OWNRISK"}}
SECTION_HINTS = {
    "Renewal": {"RENEW"}, "Cancellation": {"END", "NOTICE"}, "Refund": {"REFUND"},
    "Payment": {"MONEY", "LATE", "DEPOSIT", "PENALTY"}, "Privacy": {"DATA", "COLLECT", "SHARE", "privacy"},
    "Liability": {"LIABLE", "LOSS", "COVER", "WARRANT"}, "Disputes": {"DISPUTE", "ARBITR", "COURT"},
    "Changes": {"CHANGE", "TERMS"},
}
ACTOR_PLAIN = ("This wording gives the right, or the burden, to the other party rather than to you, "
               "so it usually works in your favour. Read the full sentence to be sure.")
DEFINITION_PLAIN = ("This looks like a definition of a term rather than a duty or a risk, "
                    "so it is shown with low attention.")
_DEFINITION_RE = re.compile(
    r"^[^.]{0,60}\b(?:means|shall mean|refers? to|is defined as|are defined as|has the meaning)\b", re.I)


def confidence_label(conf):
    return "Strong match" if conf >= 75 else "Likely" if conf >= 55 else "Possible, please review"


def analyze_clause(text, section="", mode="hybrid"):
    """Return a list of reasons (dicts) for one clause, highest level first.

    hybrid: keyword rules + whole-sentence meaning + context (negation, who the sentence is
            about, definitions, section heading). Each reason carries a confidence score.
    rules:  keyword rules only.
    """
    if mode == "rules":
        return _analyze_rules_only(text)
    sem = semantic.match_intents(text)
    hint_tokens = semantic.concept_tokens(section) if section else set()
    definition = bool(_DEFINITION_RE.search(text))
    short = len(text.split()) < 4
    best = {}
    for rule in RULES:
        m = None
        for rx in rule["patterns"]:
            m = rx.search(text)
            if m:
                break
        info = sem.get(rule["id"])
        hinted = bool(hint_tokens & SECTION_HINTS[rule["category"]])
        cos_hit = (bool(info) and info["score"] >= SEM_MIN - (0.05 if hinted else 0.0)
                   and len(info["shared"]) >= 2 and info["core_ok"])
        cos_conf = max(0.0, min(1.0, (info["score"] - 0.25) / 0.40)) * 0.85 if cos_hit else 0.0
        concept_conf = info["concept"] if info else 0.0
        sem_conf = max(cos_conf, concept_conf)
        sem_hit = sem_conf > 0
        if not m and not sem_hit:
            continue

        phrase_conf = (0.72 if rule["level"] != "Low" else 0.5) if m else 0.0
        conf = 1 - (1 - phrase_conf) * (1 - sem_conf)

        if m:
            start, end = m.start(), m.end()
            if end - start > 70:
                end = start + 70
                while end < len(text) and not text[end].isspace():
                    end += 1
            spans = [[start, end]]
            matched = re.sub(r"\s+", " ", m.group(0)).strip()
            if len(matched) > 90:
                matched = matched[:87].rstrip() + "..."
            detection = "both" if sem_hit else "phrase"
        else:
            chosen = info["concept_spans"] if concept_conf >= cos_conf and info["concept_spans"] else info["spans"]
            spans = [list(sp) for sp in chosen] or [[0, min(len(text), 40)]]
            start = spans[0][0]
            matched = ", ".join(text[a:b] for a, b in spans)
            matched = matched if len(matched) <= 90 else matched[:87].rstrip() + "..."
            detection = "meaning"

        negated = bool(rule["neg"] and _is_negated(text, start))
        favourable = False
        actor = RISK_ACTOR.get(rule["id"])
        if actor and info and ACTOR_EXEMPT.get(rule["id"], set()) & info["names"]:
            actor = None        # e.g. "you use it at your own risk": the sentence is about you, and it is a risk
        if actor and not negated:
            party = semantic.party_near(text, start)
            favourable = (actor == "other" and party == "you") or (actor == "you" and party == "other")
        notes = []
        if hinted:
            conf = min(0.98, conf + 0.08)
            notes.append(f"The section heading ({section}) points the same way.")
        if definition:
            conf -= 0.30
        if short:
            conf -= 0.15
        if conf < MIN_CONF:
            continue

        level = rule["level"]
        protective = bool(info and info["guard"])
        if protective and not (negated or favourable or definition):
            level = {"High": "Medium", "Medium": "Low", "Low": "Low"}[level]
            notes.append("Protective wording in the same sentence lowers the attention level.")
        if negated or favourable or definition:
            level = "Low"
        elif level == "High" and conf < 0.60:
            level = "Medium"
        explanation = (NEGATED_PLAIN if negated else ACTOR_PLAIN if favourable
                       else DEFINITION_PLAIN if definition else rule["plain"])
        cand = {
            "category": rule["category"], "level": level, "title": rule["title"], "matched": matched,
            "explanation": explanation, "negated": negated, "favourable": favourable,
            "span": spans[0], "spans": spans, "confidence": round(conf * 100),
            "confidence_label": confidence_label(round(conf * 100)), "detection": detection,
            "similar_to": info["example"] if info and detection != "phrase" else "", "notes": notes,
        }
        key = (LEVELS[level], cand["confidence"])
        cur = best.get(rule["category"])
        if cur is None or key > (LEVELS[cur["level"]], cur["confidence"]):
            best[rule["category"]] = cand
    reasons = list(best.values())
    reasons.sort(key=lambda r: (-LEVELS[r["level"]], -r["confidence"], CATEGORIES.index(r["category"])))
    return reasons


def _top_level(reasons):
    return LEVEL_NAMES[max(LEVELS[r["level"]] for r in reasons)] if reasons else ""


def _segments(text, reasons):
    """Split text into [chunk, level] runs so highlights can be drawn safely."""
    marks = [0] * len(text)
    for r in reasons:
        for s, e in r.get("spans") or [r["span"]]:
            for i in range(s, min(e, len(text))):
                marks[i] = max(marks[i], LEVELS[r["level"]])
    segs, i = [], 0
    while i < len(text):
        j = i
        while j < len(text) and marks[j] == marks[i]:
            j += 1
        segs.append([text[i:j], LEVEL_NAMES.get(marks[i], "")])
        i = j
    return segs


# --------------------------------------------------------------------------
# 4. Obligations
# --------------------------------------------------------------------------
_PAY_WORDS = re.compile(
    r"\b(?:pay\w*|rent|fees?|charges?|deposit|price|cost|due|invoice\w*|penalt\w+|interest|subscription|"
    r"premium|instal+ments?|deduct\w*|refund\w*|bill\w*|salary|wages?|compensation)\b", re.I)
_FREQUENCY = [
    (re.compile(r"\b(?:per|each|every|a)\s+month\b|\bmonthly\b", re.I), "Monthly"),
    (re.compile(r"\b(?:per|each|every|a)\s+week\b|\bweekly\b", re.I), "Weekly"),
    (re.compile(r"\b(?:per|each|every|a)\s+quarter\b|\bquarterly\b", re.I), "Quarterly"),
    (re.compile(r"\b(?:per|each|every|a)\s+year\b|\bannual(?:ly)?\b|\byearly\b", re.I), "Yearly"),
    (re.compile(r"\bone[- ]time\b|\bonce\b|\bupfront\b|\blump[- ]sum\b", re.I), "One time"),
]
_DUE_RE = re.compile(
    r"\b(?:due|payable|paid)\s+(?:on|by|before|within|in\s+advance|upfront|at\s+the\s+(?:start|beginning|end)|"
    r"no\s+later\s+than)[^.;]{0,60}", re.I)

_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15,
         "twenty": 20, "thirty": 30, "forty": 40, "fortyfive": 45, "fifty": 50, "sixty": 60,
         "seventy": 70, "ninety": 90}
_NUM_ALT = "|".join(sorted([w.replace("fortyfive", "forty[- ]five") for w in _NUMS], key=len, reverse=True))
_NOTICE_NUM = re.compile(
    rf"\b(\d{{1,3}}|{_NUM_ALT})\s*(?:\(\s*\d+\s*\)\s*)?[- ]?"
    r"(business\s+days?|working\s+days?|calendar\s+days?|days?|weeks?|months?|years?)\b", re.I)
_NOT_NOTICE = re.compile(
    r"(?:['\u2019]s?)?\s*-?\s*(?:terms?|lease|contract|subscription|agreement|plan|renewal|rent|salary|pay|fees?|worth|charges?)\b", re.I)
_NOTICE_WORD = re.compile(r"\bnotice\b|\bnotif(?:y|ication)\b", re.I)
_NOTICE_ABOUT = [
    (re.compile(r"cancel|terminat", re.I), "Cancellation or termination"),
    (re.compile(r"renew", re.I), "Non-renewal"),
    (re.compile(r"vacat|quit|leave|move\s+out", re.I), "Moving out"),
    (re.compile(r"increase|change|amend|modif", re.I), "Changes to terms or price"),
]


def _kind_of(sentence):
    if re.search(r"\brefund", sentence, re.I):
        return "Refund"
    if re.search(r"\bdeposit\b", sentence, re.I):
        return "Deposit"
    if re.search(r"\b(?:late|penalt\w*|fees?|charges?|interest|surcharge)\b", sentence, re.I):
        return "Fee or charge"
    return "Payment"


def extract_obligations(clauses):
    """Find payment amounts, payment timing and notice periods using patterns."""
    payments, notices, seen_p, seen_n = [], [], set(), set()
    for c in clauses:
        s = c["text"]
        where = {"page": c["page"], "ref": c["ref"], "text": s}
        if _PAY_WORDS.search(s):
            freq = next((label for rx, label in _FREQUENCY if rx.search(s)), "")
            due = _DUE_RE.search(s)
            condition = re.sub(r"\s+", " ", due.group(0)).strip() if due else ""
            found = [m.group(0).strip().rstrip(",") for m in AMOUNT_RE.finditer(s)]
            if re.search(r"\b(?:fees?|interest|penalt\w*|deposit|charges?|increase|late)\b", s, re.I):
                found += [m.group(0).strip() for m in PERCENT_RE.finditer(s)]
            for amount in found:
                key = (amount, s)
                if key in seen_p:
                    continue
                seen_p.add(key)
                payments.append({"amount": amount, "kind": _kind_of(s),
                                 "frequency": "" if amount.endswith(("%", "t")) and PERCENT_RE.fullmatch(amount) else freq,
                                 "condition": condition, **where})
        if _NOTICE_WORD.search(s):
            about = next((label for rx, label in _NOTICE_ABOUT if rx.search(s)), "General notice")
            for m in _NOTICE_NUM.finditer(s):
                if _NOT_NOTICE.match(s[m.end():m.end() + 24]):
                    continue  # e.g. "twelve-month terms" or "two months' rent"
                raw = m.group(1)
                number = int(raw) if raw.isdigit() else _NUMS.get(re.sub(r"[- ]", "", raw.lower()))
                if number is None:
                    continue
                unit = re.sub(r"\s+", " ", m.group(2).lower())
                period = f"{number} {unit}"
                key = (period, s)
                if key in seen_n:
                    continue
                seen_n.add(key)
                notices.append({"period": period, "about": about, **where})
    return payments, notices


# --------------------------------------------------------------------------
# 5. Whole-document analysis and scoring
# --------------------------------------------------------------------------
def score_from_counts(high, medium, low, total_clauses=None):
    """
    Computes a balanced contract risk score (0-100) reflecting provision density.
    A single high risk provision gives significant attention (e.g. 35-45),
    while multiple severe clauses push toward 70-100 without artificial clipping.
    """
    if high == 0 and medium == 0 and low == 0:
        return 0, "Low"
    
    # Weighted base risk index
    raw_points = (high * 22) + (medium * 8) + (low * 2)
    
    if total_clauses and total_clauses > 0:
        # Scale with clause density for long contracts
        density_factor = min(1.3, max(0.85, 1.0 + (high + medium) / float(total_clauses)))
        raw_points = raw_points * density_factor

    # Logarithmic-style progressive dampening so score scales smoothly up to 100
    if raw_points <= 40:
        score = int(raw_points)
    elif raw_points <= 100:
        score = int(40 + (raw_points - 40) * 0.7)
    else:
        score = int(82 + min(18, (raw_points - 100) * 0.25))
        
    score = max(5, min(100, score))
    level = "High" if score >= 55 or high >= 2 else "Medium" if score >= 25 or high == 1 or medium >= 2 else "Low"
    return score, level


def analyze_document(pages):
    clauses, truncated = build_clauses(pages)
    flagged, counts, cats = [], {"High": 0, "Medium": 0, "Low": 0}, {}
    for c in clauses:
        reasons = analyze_clause(c["text"], c.get("section", ""))
        if not reasons:
            continue
        for r in reasons:
            counts[r["level"]] += 1
            cats[r["category"]] = cats.get(r["category"], 0) + 1
        flagged.append({**c, "level": _top_level(reasons), "reasons": reasons,
                        "segments": _segments(c["text"], reasons)})
    dispute_tips = {
        "cancel_provider": ("Negotiate a symmetric cancellation right so both parties have equal notice periods.", "Either party may terminate this agreement upon providing thirty (30) days' written notice."),
        "cancel_penalty": ("Request that early termination fees be capped or waived if termination is due to service disruption or breach.", "Early termination charges shall not exceed one month's standard fee."),
        "renew_auto": ("Request explicit written opt-in rather than automatic multi-year rollover.", "This agreement shall expire at the end of the term unless renewed by mutual written consent of both parties."),
        "renew_optout": ("Request that the opt-out notice window be shortened to 15 or 30 days.", "Either party may elect not to renew by providing written notice at least thirty (30) days prior to the expiration date."),
        "refund_none": ("Insist on prorated refunds if service is cancelled without cause or if provider breaches.", "In the event of early termination by the provider without cause, fees paid in advance shall be refunded on a pro-rata basis."),
        "privacy_share": ("Insert strict restrictions limiting third-party sharing exclusively to core service delivery with no data sale.", "Personal data shall not be sold, licensed, or shared with third parties for marketing purposes without prior explicit consent."),
        "liab_limit": ("Ensure the limitation of liability is mutual and excludes gross negligence, willful misconduct, or data breaches.", "Neither party's aggregate liability under this agreement shall exceed the total fees paid in the preceding twelve (12) months, excluding breaches of confidentiality or gross negligence."),
        "liab_indemnity": ("Limit indemnification to final court-adjudicated third-party claims and remove unilateral attorney fee obligations.", "Each party agrees to indemnify the other solely against third-party claims arising directly from proven willful misconduct."),
        "change_unilateral": ("Require at least 30 days advance written notice for any amendments and the right to terminate without penalty if changes are unacceptable.", "Provider may amend these terms upon thirty (30) days' prior written notice. If User objects, User may terminate the agreement without penalty prior to the effective date."),
    }

    payments, notices = extract_obligations(clauses)
    score, level = score_from_counts(counts["High"], counts["Medium"], counts["Low"], total_clauses=len(clauses))
    has_pages = bool(pages) and pages[0][0] is not None

    # Attach recommendations / rephrase advice to flagged clauses
    for c in flagged:
        for r in c["reasons"]:
            # find rule key if possible or match category
            cat = r.get("category", "")
            title = r.get("title", "").lower()
            tip = None
            rephrase = None
            if "indemni" in title or "cover the other" in title:
                tip, rephrase = dispute_tips["liab_indemnity"]
            elif "renew" in title or "renew" in cat.lower():
                tip, rephrase = dispute_tips["renew_auto"]
            elif "cancel" in title or "terminat" in title:
                tip, rephrase = dispute_tips["cancel_provider"]
            elif "dispute" in cat.lower() or "arbitrat" in title:
                tip, rephrase = ("Consider retaining local small claims court rights or mutual mediation before binding arbitration.", "Any dispute arising hereunder shall first be submitted to good-faith mediation prior to formal proceedings.")
            elif "liabilit" in cat.lower() or "responsibility" in title:
                tip, rephrase = dispute_tips["liab_limit"]
            elif "change" in cat.lower() or "unilateral" in title:
                tip, rephrase = dispute_tips["change_unilateral"]
            elif "privacy" in cat.lower() or "data" in title:
                tip, rephrase = dispute_tips["privacy_share"]
            elif "refund" in cat.lower():
                tip, rephrase = dispute_tips["refund_none"]
            
            if tip:
                r["recommendation"] = tip
                r["suggested_rephrase"] = rephrase

    # Build key takeaways / AI-style executive summary
    takeaways = []
    if counts["High"] > 0:
        high_cats = list({r["category"] for c in flagged for r in c["reasons"] if r["level"] == "High"})
        takeaways.append(f"Contains {counts['High']} High-Risk item{'s' if counts['High']>1 else ''} primarily involving {', '.join(high_cats)}.")
    if notices:
        takeaways.append(f"Notice requirements detected: {len(notices)} deadline{'s' if len(notices)>1 else ''} including {notices[0]['period']} ({notices[0]['about']}).")
    if payments:
        takeaways.append(f"Key financial obligations: {len(payments)} payment term{'s' if len(payments)>1 else ''} recorded, starting with {payments[0]['amount']} ({payments[0]['kind']}).")
    if not takeaways:
        takeaways.append("No critical standard risks or automatic renewal traps detected by predefined rules.")

    return {
        "page_count": len(pages) if has_pages else None,
        "clause_count": len(clauses),
        "truncated": truncated,
        "summary": {"high": counts["High"], "medium": counts["Medium"], "low": counts["Low"],
                    "score": score, "level": level, "takeaways": takeaways},
        "categories": [{"name": n, "count": cats[n]} for n in CATEGORIES if n in cats],
        "flagged": flagged,
        "payments": payments,
        "notices": notices,
    }


# --------------------------------------------------------------------------
# 6. Version comparison
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 6. Intelligent Section & Clause Matching & Version Comparison
# --------------------------------------------------------------------------

def _norm(text):
    return re.sub(r"\W+", " ", text.lower()).strip()


def word_diff(old, new):
    """Return [(kind, text)] with kind in same / del / ins."""
    a, b = re.findall(r"\s+|\S+", old), re.findall(r"\s+|\S+", new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.append(("same", "".join(a[i1:i2])))
        else:
            if i2 > i1:
                out.append(("del", "".join(a[i1:i2])))
            if j2 > j1:
                out.append(("ins", "".join(b[j1:j2])))
    return out


def _view(clause):
    reasons = analyze_clause(clause["text"], clause.get("section", ""))
    return {"clause": clause, "level": _top_level(reasons),
            "reasons": [{k: r[k] for k in ("category", "level", "title")} for r in reasons]}

def build_comparison_clauses(pages):
    """
    Groups raw text by complete logical sections or numbered paragraphs rather than isolated sentences.
    Preserves section numbers and titles for accurate clause matching.
    """
    raw_clauses, _ = build_clauses(pages)
    if not raw_clauses:
        return []

    grouped = []
    curr = None

    for idx, c in enumerate(raw_clauses):
        sec = (c.get("section") or "").strip()
        ref = (c.get("ref") or "").strip()
        
        # Decide if this belongs to the current group or starts a new clause
        is_same_group = False
        if curr:
            if ref and curr.get("ref") == ref:
                is_same_group = True
            elif not ref and sec and curr.get("section") == sec:
                is_same_group = True
                
        if is_same_group and curr:
            curr["text"] = curr["text"] + " " + c["text"]
        else:
            if curr:
                grouped.append(curr)
            display_title = ""
            if ref:
                display_title = f"Clause {ref}"
                if sec:
                    display_title += f" – {sec}"
            elif sec:
                display_title = sec
            else:
                display_title = f"Unnumbered clause {len(grouped) + 1}"

            curr = {
                "id": len(grouped) + 1,
                "page": c.get("page"),
                "ref": ref,
                "section": sec,
                "display_title": display_title,
                "text": c["text"],
            }

    if curr:
        grouped.append(curr)

    return grouped


def _extract_amounts(text):
    return AMOUNT_RE.findall(text)


def _extract_days(text):
    return re.findall(r"\b(\d+)\s*[- ]?(?:days?|business\s+days?|months?)\b", text, re.I)


def _explain_clause_difference(old_text, new_text, old_reasons, new_reasons):
    """
    Generates meaningful business explanations for what changed instead of raw diff words.
    """
    exps = []
    
    # 1. Financial changes (Rent, deposits, fees)
    old_amounts = _extract_amounts(old_text)
    new_amounts = _extract_amounts(new_text)
    if old_amounts and new_amounts and old_amounts != new_amounts:
        exps.append(f"Financial commitment updated from {old_amounts[0]} to {new_amounts[0]}.")
    elif not old_amounts and new_amounts:
        exps.append(f"New financial charge or obligation introduced: {new_amounts[0]}.")
    elif old_amounts and not new_amounts:
        exps.append(f"Previous financial reference ({old_amounts[0]}) was removed.")

    # 2. Deadlines and notice periods
    old_days = _extract_days(old_text)
    new_days = _extract_days(new_text)
    if old_days and new_days and old_days != new_days:
        exps.append(f"Notice or timeline period modified from {old_days[0]} to {new_days[0]}.")

    # 3. Risk flags change
    old_cats = {r["category"]: r["level"] for r in old_reasons}
    new_cats = {r["category"]: r["level"] for r in new_reasons}

    for cat, lvl in new_cats.items():
        if cat not in old_cats:
            exps.append(f"New {lvl}-level {cat} risk condition introduced.")
        elif LEVELS.get(lvl, 0) > LEVELS.get(old_cats[cat], 0):
            exps.append(f"Liability severity increased under {cat} from {old_cats[cat]} to {lvl}.")
        elif LEVELS.get(lvl, 0) < LEVELS.get(old_cats[cat], 0):
            exps.append(f"Protective wording lowered {cat} exposure from {old_cats[cat]} to {lvl}.")

    for cat, lvl in old_cats.items():
        if cat not in new_cats:
            exps.append(f"Previous {cat} ({lvl}) exposure removed or resolved.")

    if not exps:
        exps.append("Language refined or clarified without altering core legal risk thresholds.")

    return exps


def _explain_added_clause(text, reasons):
    """Explains potential operational implications of an added provision."""
    t_lower = text.lower()
    if "auto" in t_lower and "renew" in t_lower:
        return "Agreement now renews automatically unless written opt-out notice is delivered in time."
    if "non-refundable" in t_lower or "no refund" in t_lower:
        return "New upfront charge or fee structure where funds cannot be recovered upon early termination."
    if "indemnif" in t_lower or "hold harmless" in t_lower:
        return "You may become liable to cover the other party's third-party claims, liabilities, or legal defense costs."
    if "sole discretion" in t_lower or "at any time" in t_lower:
        return "Gives other party unilateral flexibility to modify rates, rules, or service specifications."
    if "wear and tear" in t_lower:
        return "Defines standards regarding property condition upon return."
    if reasons:
        return f"Introduces new {reasons[0]['level']} risk condition concerning {reasons[0]['category']} ({reasons[0]['title']})."
    return "New contractual provision added to agreement."


def _explain_removed_clause(text, reasons):
    """Explains potential implications of a removed provision."""
    t_lower = text.lower()
    if "wear and tear" in t_lower:
        return "Removed wear-and-tear protection; tenant or user may become responsible for ordinary degradation."
    if "written consent" in t_lower or "prior notice" in t_lower:
        return "Removed written-consent or prior notice requirement; other party gains more flexibility to change terms."
    if "refund" in t_lower:
        return "Prior refund eligibility or rebate terms have been eliminated."
    if "warrant" in t_lower:
        return "Previous quality or performance warranty protections removed."
    if reasons:
        return f"Eliminates previous {reasons[0]['category']} clause ({reasons[0]['title']})."
    return "Previous contractual clause removed from draft."


def compare_documents(old, new):
    """
    Compares two clause sets using section numbers, headings, and semantic sequence matching.
    Generates change explanations and complete Risk Change Analysis.
    """
    old_keys = [_norm(c["text"]) for c in old]
    new_keys = [_norm(c["text"]) for c in new]

    # Pre-calculate reasons
    old_views = [_view(c) for c in old]
    new_views = [_view(c) for c in new]

    # 1. Exact match pass
    index = {}
    for i, k in enumerate(old_keys):
        index.setdefault(k, []).append(i)

    used_old, new_left, unchanged = set(), [], 0
    for j, k in enumerate(new_keys):
        bucket = index.get(k)
        if bucket:
            used_old.add(bucket.pop(0))
            unchanged += 1
        else:
            new_left.append(j)

    old_left = [i for i in range(len(old)) if i not in used_old]

    # 2. Section number / Heading heuristic matching
    pairs, taken_o, taken_n = [], set(), set()

    for j in new_left:
        ref_n = new[j].get("ref")
        sec_n = new[j].get("section")
        if not ref_n and not sec_n:
            continue
        for i in old_left:
            if i in taken_o:
                continue
            ref_o = old[i].get("ref")
            sec_o = old[i].get("section")
            if (ref_n and ref_o and ref_n == ref_o) or (sec_n and sec_o and sec_n == sec_o and len(sec_n) > 3):
                # Matched by explicit clause number or heading
                sm = difflib.SequenceMatcher(None, old_keys[i].split(), new_keys[j].split(), autojunk=False)
                ratio = sm.ratio()
                pairs.append((j, i, ratio))
                taken_o.add(i)
                taken_n.add(j)
                break

    # 3. Similarity matcher for remaining unmatched clauses
    rem_old = [i for i in old_left if i not in taken_o]
    rem_new = [j for j in new_left if j not in taken_n]

    if len(rem_old) <= MAX_COMPARE_UNMATCHED and len(rem_new) <= MAX_COMPARE_UNMATCHED:
        old_tok = {i: old_keys[i].split() for i in rem_old}
        cands = []
        for j in rem_new:
            tb = new_keys[j].split()
            sm = difflib.SequenceMatcher(None, autojunk=False)
            sm.set_seq2(tb)
            for i in rem_old:
                sm.set_seq1(old_tok[i])
                if sm.real_quick_ratio() < 0.40:
                    continue
                r = sm.ratio()
                if r >= 0.40:
                    cands.append((r, i, j))
        cands.sort(key=lambda t: (-t[0], t[2], t[1]))
        for r, i, j in cands:
            if i in taken_o or j in taken_n:
                continue
            taken_o.add(i)
            taken_n.add(j)
            pairs.append((j, i, r))

    matched_o = {i for _, i, _ in pairs}
    matched_n = {j for j, _, _ in pairs}

    modified = []
    for j, i, r in sorted(pairs):
        o_view = old_views[i]
        n_view = new_views[j]
        exps = _explain_clause_difference(old[i]["text"], new[j]["text"], o_view["reasons"], n_view["reasons"])
        
        modified.append({
            "old": old[i],
            "new": new[j],
            "similarity": round(r * 100),
            "display_title": new[j].get("display_title") or old[i].get("display_title") or f"Clause {j+1}",
            "segments": word_diff(old[i]["text"], new[j]["text"]),
            "old_level": o_view["level"],
            "new_level": n_view["level"],
            "reasons": n_view["reasons"],
            "explanations": exps,
        })

    added = []
    for j in new_left:
        if j not in matched_n:
            n_view = new_views[j]
            added.append({
                "clause": new[j],
                "level": n_view["level"],
                "reasons": n_view["reasons"],
                "display_title": new[j].get("display_title") or f"Added Clause {j+1}",
                "significance": _explain_added_clause(new[j]["text"], n_view["reasons"]),
            })

    removed = []
    for i in old_left:
        if i not in matched_o:
            o_view = old_views[i]
            removed.append({
                "clause": old[i],
                "level": o_view["level"],
                "reasons": o_view["reasons"],
                "display_title": old[i].get("display_title") or f"Removed Clause {i+1}",
                "significance": _explain_removed_clause(old[i]["text"], o_view["reasons"]),
            })

    # 4. Risk Change Analysis calculation
    old_high = sum(1 for v in old_views if v["level"] == "High")
    old_med = sum(1 for v in old_views if v["level"] == "Medium")
    new_high = sum(1 for v in new_views if v["level"] == "High")
    new_med = sum(1 for v in new_views if v["level"] == "Medium")

    old_overall = "High" if old_high >= 2 else "Medium" if (old_high == 1 or old_med >= 2) else "Low"
    new_overall = "High" if new_high >= 2 else "Medium" if (new_high == 1 or new_med >= 2) else "Low"

    # Distinguish categories
    new_risks = [a["significance"] for a in added if a["level"] in ("High", "Medium")]
    resolved_risks = [r["significance"] for r in removed if r["level"] in ("High", "Medium")]
    increased = [m["display_title"] for m in modified if LEVELS.get(m["new_level"], 0) > LEVELS.get(m["old_level"], 0)]
    reduced = [m["display_title"] for m in modified if LEVELS.get(m["new_level"], 0) < LEVELS.get(m["old_level"], 0)]

    notable_summary = []
    if new_risks:
        notable_summary.extend(new_risks[:3])
    if increased:
        notable_summary.append(f"Severity increased in {len(increased)} modified clause(s).")
    if resolved_risks:
        notable_summary.append(f"{len(resolved_risks)} previous liability provision(s) eliminated.")
    if not notable_summary:
        notable_summary.append("General wording updates with minimal change to overall liability threshold.")

    risk_analysis = {
        "old_level": old_overall,
        "new_level": new_overall,
        "old_high": old_high,
        "new_high": new_high,
        "notable_changes": notable_summary,
        "new_risks_count": len(new_risks),
        "resolved_risks_count": len(resolved_risks),
        "increased_count": len(increased),
        "reduced_count": len(reduced),
    }

    return {
        "unchanged": unchanged,
        "modified": modified,
        "added": added,
        "removed": removed,
        "risk_analysis": risk_analysis,
        "note": "",
    }