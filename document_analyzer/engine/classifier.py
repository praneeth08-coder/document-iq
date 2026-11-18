"""DocumentIQ 2.0 - Document Type Classification.

Classifies incoming documents into granular legal & non-legal types:
- Legal Contract / Agreement
- Consultancy Agreement
- Master Services Agreement (MSA)
- Employment Agreement
- Non-Disclosure Agreement (NDA)
- Software / SaaS Licence Agreement
- Business Proposal
- Technical Documentation
- Company Policy
- Financial Document
- Other / Unknown

Prevents technical specifications or guides from being penalized as legal contracts.
"""
import re
from typing import Dict, Any

DOC_TYPES = [
    "Consultancy Agreement",
    "Service Agreement",
    "Non-Disclosure Agreement (NDA)",
    "Employment Agreement",
    "Software & Licence Agreement",
    "Commercial Lease",
    "Legal Contract",
    "Business Proposal",
    "Technical Documentation",
    "Company Policy",
    "Financial Document",
    "General Document"
]

# Signatures for technical documents that should NOT be analyzed as contracts
_TECH_PATTERNS = [
    r"\b(?:architecture|system\s+design|api\s+documentation|user\s+manual|technical\s+spec|specifications|sdk|code\s+snippet|endpoint|database\s+schema|readme|dockerfile|deployment\s+guide|pull\s+request|git\s+repository)\b",
    r"\b(?:class\s+\w+|def\s+\w+\(|import\s+[a-z0-9_]+|function\s+\w+|curl\s+https?:|json\s+payload|schema\.prisma)\b",
    r"\b(?:requirements\s+traceability|sprint\s+backlog|jira\s+ticket|acceptance\s+criteria)\b",
]

_LEGAL_CONTRACT_MARKERS = [
    r"\b(?:this\s+agreement|by\s+and\s+between|in\s+witness\s+whereof|effective\s+date|now,\s+therefore|covenants?\s+and\s+agreements?)\b",
    r"\b(?:parties\s+agree|agrees\s+as\s+follows|legally\s+bound|hereby\s+agree)\b",
    r"\b(?:governing\s+law|jurisdiction\s+of\s+the\s+courts?|severability|entire\s+agreement)\b",
]

_TYPE_KEYWORDS = {
    "Non-Disclosure Agreement (NDA)": [
        r"\b(?:non[- ]disclosure|confidentiality\s+agreement|proprietary\s+information\s+agreement|disclosing\s+party|receiving\s+party)\b"
    ],
    "Consultancy Agreement": [
        r"\b(?:consultan(?:t|cy)|advisory\s+services|statement\s+of\s+work|client\s+and\s+consultant|consulting\s+fee)\b"
    ],
    "Service Agreement": [
        r"\b(?:master\s+services?\s+agreement|msa|service\s+provider|statement\s+of\s+work|sow|client\s+and\s+provider)\b"
    ],
    "Employment Agreement": [
        r"\b(?:employment\s+agreement|offer\s+of\s+employment|employee\s+and\s+employer|salary|job\s+title|probationary\s+period|non[- ]compete)\b"
    ],
    "Software & Licence Agreement": [
        r"\b(?:end\s+user\s+licen[sc]e|eula|software\s+licen[sc]e|saas\s+agreement|cloud\s+services\s+agreement|subscriber|hosted\s+service)\b"
    ],
    "Commercial Lease": [
        r"\b(?:lease\s+agreement|tenancy\s+agreement|landlord|tenant|demised\s+premises|monthly\s+rent|security\s+deposit)\b"
    ],
    "Company Policy": [
        r"\b(?:employee\s+handbook|code\s+of\s+conduct|privacy\s+policy|acceptable\s+use\s+policy|terms\s+of\s+service|cookie\s+policy)\b"
    ],
    "Business Proposal": [
        r"\b(?:request\s+for\s+proposal|rfp|project\s+proposal|executive\s+summary|deliverables\s+and\s+timeline|budget\s+estimate)\b"
    ],
    "Financial Document": [
        r"\b(?:balance\s+sheet|profit\s+and\s+loss|income\s+statement|auditor'?s?\s+report|quarterly\s+financial|promissory\s+note|loan\s+agreement)\b"
    ],
}


def classify_document(text: str, filename: str = "") -> Dict[str, Any]:
    """Classify document and determine if it represents a contractual instrument."""
    sample = (filename + "\n" + text[:6000]).lower()
    
    # 1. Technical Documentation check
    tech_hits = sum(len(re.findall(p, sample, re.I)) for p in _TECH_PATTERNS)
    contract_hits = sum(len(re.findall(p, sample, re.I)) for p in _LEGAL_CONTRACT_MARKERS)
    
    if tech_hits >= 2 and contract_hits == 0:
        return {
            "type": "Technical Documentation",
            "is_contract": False,
            "confidence": 90,
            "description": "Technical project documentation or engineering specifications.",
            "recommended_action": "General document summary provided; contractual risk scoring skipped."
        }

    # 2. Check specific contract types
    best_type = None
    best_score = 0
    for doc_type, patterns in _TYPE_KEYWORDS.items():
        score = sum(len(re.findall(p, sample, re.I)) for p in patterns)
        if score > best_score:
            best_score = score
            best_type = doc_type

    if best_score >= 1 and (contract_hits >= 1 or best_score >= 2):
        is_contract = best_type not in ("Company Policy", "Business Proposal", "Financial Document")
        return {
            "type": best_type,
            "is_contract": is_contract,
            "confidence": min(98, 65 + best_score * 10),
            "description": f"Identified as {best_type} based on contractual structure and terminology.",
            "recommended_action": "Full contract risk & obligation analysis performed." if is_contract else "Informational policy/proposal review."
        }

    if contract_hits >= 1:
        return {
            "type": "Legal Contract",
            "is_contract": True,
            "confidence": 75,
            "description": "Legal agreement containing operative rights and covenants.",
            "recommended_action": "Standard contract risk analysis performed."
        }

    # 3. Check for operative legal risk/obligation terms (renewal, non-refundable, liability, termination)
    operative_hits = sum(len(re.findall(p, sample, re.I)) for p in [
        r"\b(?:renews?\s+automatically|automatic\s+renewal|non[- ]refundable|liability|terminate\w*|indemnif\w*|penalty)\b"
    ])
    if operative_hits >= 1 and tech_hits == 0:
        return {
            "type": "Legal Contract",
            "is_contract": True,
            "confidence": 75,
            "description": "Commercial agreement containing binding contractual terms and conditions.",
            "recommended_action": "Standard contract risk analysis performed."
        }

    return {
        "type": "General Document",
        "is_contract": False,
        "confidence": 60,
        "description": "General text or business material without explicit contractual obligations.",
        "recommended_action": "General content analysis performed."
    }
