"""DocumentIQ 2.0 - Core Engine Orchestrator.

Clean, modular facade that unifies:
- Ingestion & Text Extraction (PDF, DOCX, TXT)
- Document Classification (Contract vs Technical Specification)
- Clause Structure Extraction
- Contextual Risk Detection
- Financial & Obligation Extraction
- Centralized Scoring & Deduplication
- AI Assistance & Deep Nuance Analysis
"""
import json
from typing import List, Tuple, Dict, Any

from engine.ingestion import extract_pages, detect_type, ExtractionError
from engine.classifier import classify_document
from engine.structure import extract_clauses
from engine.risk_detector import detect_clause_risks, CATEGORIES, SEVERITY_LEVELS
from engine.financial import extract_financials_and_obligations
from engine.scoring import calculate_risk_score
from engine.ai_service import ai_service


def analyze_document_pipeline(pages: List[Tuple[Any, str]], filename: str = "") -> Dict[str, Any]:
    """Execute the full DocumentIQ 2.0 analysis pipeline."""
    # 1. Reconstruct document text for classification
    full_text = "\n\n".join(t for _, t in pages)
    doc_meta = classify_document(full_text, filename=filename)

    # 2. Extract clauses and document structure
    clauses, truncated = extract_clauses(pages)

    # 3. If document is non-contractual (e.g. Technical Documentation), generate clean general report
    if not doc_meta.get("is_contract", True):
        return {
            "page_count": len(pages) if (pages and pages[0][0] is not None) else None,
            "clause_count": len(clauses),
            "truncated": truncated,
            "document_type": doc_meta["type"],
            "is_contract": False,
            "classification": doc_meta,
            "summary": {
                "critical": 0, "high": 0, "medium": 0, "low": 0,
                "score": 0, "level": "Low",
                "takeaways": [
                    f"Document classified as {doc_meta['type']}.",
                    "No contractual liability risk assessment applied to technical or informational content."
                ],
                "score_explanation": "Technical/general document. Contract risk scoring bypassed."
            },
            "categories": [],
            "flagged": [],
            "payments": [],
            "notices": [],
            "ai_available": ai_service.is_available()
        }

    # 4. Contextual Risk Detection & Deduction
    flagged = []
    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    cat_counts = {}

    for c in clauses:
        reasons = detect_clause_risks(c)
        if not reasons:
            continue

        for r in reasons:
            lvl = r["level"]
            counts[lvl] = counts.get(lvl, 0) + 1
            cat_counts[r["category"]] = cat_counts.get(r["category"], 0) + 1

        # Highest severity in clause
        top_lvl = sorted(reasons, key=lambda x: -SEVERITY_LEVELS.get(x["level"], 1))[0]["level"]
        
        # Build highlights segments
        text = c["text"]
        marks = [0] * len(text)
        for r in reasons:
            s_idx, e_idx = r["span"]
            for i in range(max(0, s_idx), min(e_idx, len(text))):
                marks[i] = max(marks[i], SEVERITY_LEVELS.get(r["level"], 1))
        
        segs = []
        i = 0
        lvl_map = {4: "Critical", 3: "High", 2: "Medium", 1: "Low"}
        while i < len(text):
            j = i
            while j < len(text) and marks[j] == marks[i]:
                j += 1
            segs.append([text[i:j], lvl_map.get(marks[i], "")])
            i = j

        flagged.append({
            **c,
            "level": top_lvl,
            "reasons": reasons,
            "segments": segs
        })

    # 5. Financial & Notice Obligations Extraction
    payments, notices = extract_financials_and_obligations(clauses)

    # 6. Centralized Risk Scoring
    score, level, score_breakdown = calculate_risk_score(
        counts.get("Critical", 0),
        counts.get("High", 0),
        counts.get("Medium", 0),
        counts.get("Low", 0),
        total_clauses=len(clauses)
    )

    # 7. Executive Takeaways
    takeaways = []
    if counts.get("Critical", 0) > 0:
        crit_cats = list({r["category"] for f in flagged for r in f["reasons"] if r["level"] == "Critical"})
        takeaways.append(f"Contains {counts['Critical']} Critical risk item(s) primarily involving {', '.join(crit_cats)}.")
    if counts.get("High", 0) > 0:
        high_cats = list({r["category"] for f in flagged for r in f["reasons"] if r["level"] == "High"})
        takeaways.append(f"Contains {counts['High']} High-Risk provision(s) primarily involving {', '.join(high_cats)}.")
    if notices:
        takeaways.append(f"Notice deadlines detected: {len(notices)} requirement(s) including {notices[0]['period']} ({notices[0]['about']}).")
    if payments:
        takeaways.append(f"Key financial commitments: {len(payments)} terms recorded, starting with {payments[0]['amount']} ({payments[0]['kind']}).")
    if not takeaways:
        takeaways.append("Standard commercial terms. No predatory clauses or critical risks identified.")

    return {
        "page_count": len(pages) if (pages and pages[0][0] is not None) else None,
        "clause_count": len(clauses),
        "truncated": truncated,
        "document_type": doc_meta["type"],
        "is_contract": True,
        "classification": doc_meta,
        "summary": {
            "critical": counts.get("Critical", 0),
            "high": counts.get("High", 0),
            "medium": counts.get("Medium", 0),
            "low": counts.get("Low", 0),
            "score": score,
            "level": level,
            "takeaways": takeaways,
            "score_explanation": score_breakdown.get("explanation", "")
        },
        "categories": [{"name": n, "count": cat_counts[n]} for n in CATEGORIES if n in cat_counts],
        "flagged": flagged,
        "payments": payments,
        "notices": notices,
        "ai_available": ai_service.is_available()
    }
