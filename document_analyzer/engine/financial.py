"""DocumentIQ 2.0 - Financial & Obligation Extraction.

Extracts:
- Contract values, fees, retainer amounts, deposits, and currency.
- Late payment interest rates and penalties.
- Notice periods, renewal windows, deadlines, and operational dates.
"""
import re
from typing import List, Dict, Any, Tuple

_CUR = r"(?:Rs\.?|INR|\u20b9|\$|USD|EUR|\u20ac|\u00a3|GBP)"
_AMOUNT = (
    r"(?<![A-Za-z0-9])" + _CUR + r"\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:lakhs?|crores?|million|thousand|k)\b)?"
    r"|(?<![\w.,])\d[\d,]*(?:\.\d+)?\s?(?:rupees|dollars|euros|USD|INR|EUR|GBP)\b"
)
AMOUNT_RE = re.compile(_AMOUNT, re.I)
PERCENT_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?(?:%|percent\b|per\s+cent\b)", re.I)

_PAY_WORDS = re.compile(
    r"\b(?:pay\w*|rent|fees?|charges?|deposit|price|cost|due|invoice\w*|penalt\w+|interest|subscription|"
    r"premium|instal+ments?|deduct\w*|refund\w*|bill\w*|salary|wages?|compensation)\b", re.I
)

_FREQUENCY = [
    (re.compile(r"\b(?:per|each|every|a)\s+month\b|\bmonthly\b", re.I), "Monthly"),
    (re.compile(r"\b(?:per|each|every|a)\s+week\b|\bweekly\b", re.I), "Weekly"),
    (re.compile(r"\b(?:per|each|every|a)\s+quarter\b|\bquarterly\b", re.I), "Quarterly"),
    (re.compile(r"\b(?:per|each|every|a)\s+year\b|\bannual(?:ly)?\b|\byearly\b", re.I), "Yearly"),
    (re.compile(r"\bone[- ]time\b|\bonce\b|\bupfront\b|\blump[- ]sum\b", re.I), "One time"),
]

_DUE_RE = re.compile(
    r"\b(?:within\s+\d+\s+days?|upon\s+(?:receipt|signing|completion|delivery)|in\s+advance|"
    r"on\s+or\s+before\s+the\s+\d+(?:st|nd|rd|th)?|net\s+\d+|monthly\s+in\s+arrears)\b", re.I
)

_NOTICE_WORD = re.compile(r"\bnotice\b", re.I)
_NOTICE_NUM = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fourteen|fifteen|twenty|twenty[- ]one|"
    r"thirty|sixty|ninety|one[- ]hundred[- ]and[- ]twenty|one[- ]hundred[- ]and[- ]eighty)"
    r"[- ]?(days?|business\s+days?|weeks?|months?|calendar\s+days?|hours?)\b", re.I
)
_NOT_NOTICE = re.compile(r"^\s*(?:of\s+(?:rent|fees|the\s+term|term|age)|-?year|-?month\s+term)", re.I)

_NUMS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fourteen": 14, "fifteen": 15,
    "twenty": 20, "twentyone": 21, "thirty": 30, "sixty": 60, "ninety": 90,
    "onehundredandtwenty": 120, "onehundredandeighty": 180,
}

_NOTICE_ABOUT = [
    (re.compile(r"\b(?:non[- ]?renewal|opt[- ]out|prevent\s+(?:auto[- ]?)?renewal)\b", re.I), "Non-renewal"),
    (re.compile(r"\b(?:cancel|terminat|end\s+the|exit)\b", re.I), "Termination"),
    (re.compile(r"\b(?:price|fee|rent|rate)\s+(?:increase|change|adjust)\b", re.I), "Price change"),
    (re.compile(r"\b(?:amend|change\s+terms?|update\s+terms?)\b", re.I), "Terms change"),
    (re.compile(r"\b(?:vacat|move[- ]out|handover)\b", re.I), "Vacating premises"),
    (re.compile(r"\b(?:default|breach|cure)\b", re.I), "Cure breach"),
]


def _kind_of(sentence: str, match_text: str) -> str:
    s = sentence.lower()
    if "interest" in s or ("late" in s and ("fee" in s or "charge" in s)):
        return "Late fee / interest"
    if "deposit" in s:
        return "Security deposit"
    if "rent" in s:
        return "Rent"
    if "penalt" in s or "liquidated" in s:
        return "Penalty"
    if "retainer" in s:
        return "Retainer"
    if "consultan" in s:
        return "Consultancy fee"
    if "subscript" in s:
        return "Subscription fee"
    if "service" in s or "fee" in s:
        return "Service fee"
    return "Payment obligation"


def extract_financials_and_obligations(clauses: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Extract structured financial terms and notice deadlines."""
    payments = []
    notices = []
    seen_p = set()
    seen_n = set()

    for c in clauses:
        s = c["text"]
        where = {"ref": c.get("ref", ""), "page": c.get("page"), "clause_id": c.get("id"), "section": c.get("section", "")}

        # 1. Financial Amounts & Percentages
        if _PAY_WORDS.search(s):
            freq = next((label for rx, label in _FREQUENCY if rx.search(s)), "")
            due_match = _DUE_RE.search(s)
            due = due_match.group(0) if due_match else ""

            # Standard currency amounts
            for m in AMOUNT_RE.finditer(s):
                raw = m.group(0).strip()
                amt = re.sub(r"\s+", " ", raw)
                key = (amt, s[:40])
                if key in seen_p:
                    continue
                seen_p.add(key)
                kind = _kind_of(s, amt)
                payments.append({
                    "amount": amt,
                    "kind": kind,
                    "frequency": freq,
                    "condition": due,
                    "text": s,
                    "snippet": s[:120],
                    **where
                })

            # Percentage rates (e.g. 1.5% interest)
            for m in PERCENT_RE.finditer(s):
                amt = m.group(0).strip()
                key = (amt, s[:40])
                if key in seen_p:
                    continue
                seen_p.add(key)
                kind = _kind_of(s, amt)
                payments.append({
                    "amount": amt,
                    "kind": kind,
                    "frequency": freq,
                    "condition": due,
                    "text": s,
                    "snippet": s[:120],
                    **where
                })

        # 2. Notice Deadlines & Durations
        if _NOTICE_WORD.search(s):
            about = next((label for rx, label in _NOTICE_ABOUT if rx.search(s)), "General notice")
            for m in _NOTICE_NUM.finditer(s):
                if _NOT_NOTICE.match(s[m.end():m.end() + 24]):
                    continue
                raw_num = m.group(1)
                num = int(raw_num) if raw_num.isdigit() else _NUMS.get(re.sub(r"[- ]", "", raw_num.lower()))
                if num is None:
                    continue
                unit = re.sub(r"\s+", " ", m.group(2).lower())
                period = f"{num} {unit}"
                key = (period, s[:40])
                if key in seen_n:
                    continue
                seen_n.add(key)
                notices.append({
                    "period": period,
                    "about": about,
                    "text": s,
                    "snippet": s[:120],
                    **where
                })

    return payments, notices
