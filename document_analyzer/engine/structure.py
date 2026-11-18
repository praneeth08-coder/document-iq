"""DocumentIQ 2.0 - Structure & Clause Extraction.

Parses document text into sections, clauses, and numbered blocks with
faithful document references (never invented).
"""
import re
from typing import List, Tuple, Dict, Any

MAX_CLAUSES = 3500

_START_RE = re.compile(
    r"^(?:"
    r"(?:section|clause|article|paragraph|part|schedule|exhibit)\s+([0-9]+|[a-z]+|[ivxlcdm]+)"
    r"|(\d+(?:\.\d+)*\.?)"
    r"|\(([a-z0-9]+|[ivxlcdm]+)\)"
    r"|([a-z]\.)"
    r"|([ivxlcdm]+\.)"
    r")\s+",
    re.I
)

_ABBR = re.compile(r"\b(?:e\.g|i\.e|etc|vs|dept|inc|corp|ltd|co|llc|mr|mrs|ms|dr|no|sec|art|cl|para)\.", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'\u201c\u2018])")


def clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def join_lines(lines: List[str]) -> str:
    out = []
    for line in lines:
        t = line.strip()
        if not t:
            continue
        if out and out[-1] and out[-1][-1] not in "-/":
            out.append(" ")
        out.append(t)
    return "".join(out)


def get_blocks(text: str) -> List[str]:
    blocks, cur = [], []
    for line in text.splitlines():
        t = line.strip()
        if not t:
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append(cur)
    return [join_lines(b) for b in blocks]


def split_prefix(block: str) -> Tuple[str, str]:
    m = _START_RE.match(block)
    if not m:
        return "", block
    token = m.group(0).strip()
    ref = token if re.search(r"[A-Za-z0-9]", token) else ""
    if len(ref) > 1 and ref.endswith("."):
        ref = ref[:-1]
    return ref, block[m.end():].strip()


def is_heading(text: str) -> bool:
    t = text.strip()
    if t.endswith(":"):
        t = t[:-1].strip()
    words = t.split()
    if not words or len(words) > 8:
        return False
    if t[-1] in ".!?;,":
        return False
    return t[0].isupper() or t[0].isdigit()


def split_sentences(text: str) -> List[str]:
    protected = _ABBR.sub(lambda m: m.group(0)[:-1] + "\x00", text)
    parts = _SENT_SPLIT.split(protected)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


def extract_clauses(pages: List[Tuple[Any, str]]) -> Tuple[List[Dict[str, Any]], bool]:
    """Parse pages into structured clause dictionaries.
    
    Returns (clauses, truncated).
    """
    clauses = []
    section = "General Provisions"
    truncated = False

    for page_num, text in pages:
        for block in get_blocks(text):
            ref, body = split_prefix(block)
            if not body:
                continue

            if is_heading(body):
                section = (ref + ". " if ref and ref[0].isdigit() else "") + body.rstrip(":").strip()
                # Only keep as operative clause if it's an actionable mandate (e.g. "No refund")
                if not any(body.strip().lower().startswith(p) for p in ["no refund", "all sales final"]):
                    continue

            sents = split_sentences(body)
            # Check for inline headings (e.g. "1. Term and Termination. The agreement...")
            if len(sents) > 1 and len(sents[0].split()) <= 6:
                first_s = sents[0].rstrip(". :")
                if len(first_s.split()) <= 5:
                    section = (ref + ". " if ref and ref[0].isdigit() else "") + first_s
                    sents = sents[1:]

            sent_idx = 0
            for s in sents:
                words = s.split()
                if len(words) < 3 and not any(k in s.lower() for k in ["no refund", "as is"]):
                    continue

                if len(clauses) >= MAX_CLAUSES:
                    return clauses, True

                sent_idx += 1
                clause_ref = ref
                if ref:
                    clause_ref = f"{ref}.{sent_idx}" if len(sents) > 1 else str(ref)
                else:
                    clause_ref = f"§{len(clauses) + 1}"

                clauses.append({
                    "id": len(clauses) + 1,
                    "page": page_num,
                    "ref": clause_ref,
                    "section": section,
                    "text": s
                })

    return clauses, truncated
