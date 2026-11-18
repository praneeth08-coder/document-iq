"""DocumentIQ 2.0 - Comprehensive Contract Risk Detection.

Covers the 15 critical contract risk categories with full contextual awareness:
1. Automatic Renewal
2. Cancellation & Termination
3. Early Termination Penalties
4. Refund Restrictions
5. Payment Obligations
6. Late Payment Penalties
7. Privacy & Data Sharing
8. Confidentiality
9. Liability Exclusions
10. Liability Caps
11. Unlimited / Broad Indemnification
12. Unilateral Amendments
13. Service Availability & Downtime
14. Dispute Resolution
15. Intellectual Property
"""
import re
from typing import List, Dict, Any, Tuple

CATEGORIES = [
    "Automatic Renewal",
    "Cancellation",
    "Early Termination Penalties",
    "Refund Restrictions",
    "Payment Obligations",
    "Late Payment Penalties",
    "Privacy & Data Sharing",
    "Confidentiality",
    "Liability Exclusions",
    "Liability Caps",
    "Indemnification",
    "Unilateral Amendments",
    "Service Availability",
    "Dispute Resolution",
    "Intellectual Property"
]

SEVERITY_LEVELS = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}

# Negation detection
_NEG_WORDS = re.compile(
    r"\b(?:not|no|never|neither|nor|without|cannot|nothing|none|shall\s+not|will\s+not|n\'t)\b", re.I
)
_NEG_SKIP = re.compile(r"\b(?:no|not)\s+(?:later|earlier|less|more|fewer|greater|only)\b|\bnot\s+only\b", re.I)

# Mutual / Bilateral markers
_MUTUAL_RE = re.compile(
    r"\b(?:either\s+party|both\s+parties|each\s+party|the\s+parties|mutually|mutual\s+written|reasonable\s+care)\b", re.I
)

# Protective exceptions
_PROTECTIVE_EXCEPTIONS = re.compile(
    r"\b(?:nothing\s+in\s+this|except\s+for|excluding|shall\s+not\s+apply\s+to|save\s+for)\b[^.]{0,60}\b(?:fraud|gross\s+negligence|wil+ful\s+misconduct|death\s+or\s+personal\s+injury|breach\s+of\s+confidentiality)\b",
    re.I
)


def is_clause_negated(text: str, start: int = 0) -> bool:
    """Check if the clause or specific span is negated."""
    if re.match(r"^\s*(?:no\b|nothing\b|neither\b|none\b)", text, re.I):
        return True
    before = text[max(0, start - 55):start]
    cut = max(before.rfind(","), before.rfind(";"), before.rfind(":"), before.rfind("("))
    if cut != -1:
        before = before[cut + 1:]
    for sep in (" but ", " and ", " however ", " although ", " while ", " whereas "):
        idx = before.lower().rfind(sep)
        if idx != -1:
            before = before[idx + len(sep):]
    before = _NEG_SKIP.sub(" ", before)
    if _NEG_WORDS.search(before):
        return True
    if start <= 15 and _NEG_WORDS.search(text[:min(len(text), 40)]):
        return True
    return False


class RiskRule:
    def __init__(self, rule_id: str, category: str, severity: str, title: str,
                 explanation: str, impact: str, recommendation: str, rephrase: str,
                 patterns: List[str], supports_negation: bool = True):
        self.rule_id = rule_id
        self.category = category
        self.severity = severity
        self.title = title
        self.explanation = explanation
        self.impact = impact
        self.recommendation = recommendation
        self.rephrase = rephrase
        self.patterns = [re.compile(p, re.I) for p in patterns]
        self.supports_negation = supports_negation


# Core Rules Catalog
RULES: List[RiskRule] = [
    # 1. Automatic Renewal
    RiskRule(
        "renew_auto", "Automatic Renewal", "High", "Automatic Renewal Trap",
        "The agreement automatically renews for successive multi-year periods unless notice is given.",
        "Unexpected financial commitment or extended lock-in period.",
        "Require explicit mutual written renewal rather than automatic evergreen rollover.",
        "This agreement shall expire at the end of the term unless renewed by mutual written agreement of both parties.",
        [r"\bauto[- ]?renew\w*", r"\brenew\w*\s+automatically\b", r"\bautomatic(?:ally)?\s+(?:renew|extend|continu)\w*",
         r"\bautomatic\s+renewal\b", r"\bevergreen\b", r"\b(?:renew|extend)\w*\s+(?:for\s+)?(?:successive|consecutive)\s+\w+"]
    ),
    # 2. Cancellation & Termination
    RiskRule(
        "cancel_unilateral", "Cancellation", "High", "Unilateral Termination at Will",
        "The other party may terminate or suspend services at its sole discretion without cause or notice.",
        "Loss of critical services or unilateral contract cancellation without recourse.",
        "Negotiate symmetric cancellation rights with reasonable advance notice (e.g., 30 days).",
        "Either party may terminate this agreement upon providing thirty (30) days' prior written notice.",
        [r"\b(?:terminate|suspend|cancel)\w*\b[^.]{0,80}\b(?:at\s+any\s+time|sole\s+discretion|without\s+(?:any\s+)?(?:prior\s+)?(?:notice|cause|reason|liability))\b",
         r"\bsole\s+discretion\b[^.]{0,60}\b(?:terminat|suspend|cancel)\w*"]
    ),
    RiskRule(
        "cancel_restricted", "Cancellation", "High", "Restricted or Prohibited Cancellation",
        "The contract specifies that it is non-cancellable or cannot be ended early under any circumstances.",
        "Inability to exit the contract even if service needs change.",
        "Insert standard termination for cause provisions upon material breach.",
        "Either party may terminate immediately upon written notice if the other party materially breaches this agreement.",
        [r"\bnon[- ]?cancell?able\b", r"\birrevocabl\w*", r"\bno\s+(?:right\s+to\s+)?(?:cancell?ation|cancel|terminate)\b",
         r"\b(?:cannot|can\s*not|may\s+not|shall\s+not)\s+(?:be\s+)?(?:cancell?ed|terminated)\b"]
    ),
    RiskRule(
        "cancel_notice", "Cancellation", "Low", "Termination Notice Requirement",
        "A standard notice period applies to terminate or end the agreement.",
        "Operational deadline tracking required to avoid unintended extension.",
        "Note the notice deadline in calendar reminders.",
        "Written notice shall be delivered via email or registered post.",
        [r"\b(?:written|prior|advance|\d+[- ]?day|thirty[- ]day)\s+notice\b",
         r"\bnotice\s+(?:period|of\s+(?:termination|cancell?ation|non[- ]?renewal))\b"]
    ),
    # 3. Early Termination Penalties
    RiskRule(
        "cancel_penalty", "Early Termination Penalties", "High", "Early Termination Financial Penalty",
        "Imposes heavy liquidated damages or demands acceleration of all future term payments upon early termination.",
        "Severe exit cost even if service quality is unacceptable.",
        "Ensure termination penalties are waived for breach, or capped to actual direct costs.",
        "Early termination charges shall not apply if termination is due to provider's uncured default.",
        [r"\b(?:early\s+)?(?:termination|cancell?ation|exit)\s+(?:fee|charge|penalt(?:y|ies))\b",
         r"\bliquidated\s+damages\b", r"\bpay\w*\s+(?:the\s+)?(?:remaining|balance|rest)\b[^.]{0,40}\b(?:term|contract|rent|payments?)\b",
         r"\baccelerat\w*\s+(?:all\s+)?(?:future\s+)?(?:rent|payments?|fees)\b"]
    ),
    # 4. Refund Restrictions
    RiskRule(
        "refund_none", "Refund Restrictions", "High", "Strict No-Refund Policy",
        "All fees paid are strictly non-refundable regardless of circumstance or service failure.",
        "Unrecoverable advance payments if services are terminated or defective.",
        "Demand pro-rata refunds of unearned prepaid fees upon early termination.",
        "In the event of termination without cause by provider, prepaid fees shall be refunded on a pro-rata basis.",
        [r"\bnon[- ]?refundable\b", r"\bno\s+refunds?\b", r"\bwithout\s+(?:any\s+)?refund\b", r"\bforfeit\w*\b"]
    ),
    # 5. Payment Obligations
    RiskRule(
        "pay_duty", "Payment Obligations", "Low", "Standard Payment Obligation",
        "Commercial obligation to pay fees, rent, or invoices for provided services.",
        "Normal operational expenditure.",
        "Verify invoicing accuracy and payment due windows.",
        "Payment shall be made within 30 days of receiving a valid tax invoice.",
        [r"\b(?:shall|must|agrees?\s+to|will)\s+(?:\w+\s+){0,2}pay\w*\b", r"\bpayable\b", r"\binvoice\w*\b"]
    ),
    # 6. Late Payment Penalties
    RiskRule(
        "pay_late", "Late Payment Penalties", "Medium", "Late Payment Fees & Interest",
        "Applies interest charges or penalties for overdue invoices.",
        "Additional fees if invoice processing is delayed.",
        "Ensure late fee rates comply with statutory limits (e.g. 1.5% per month) and include grace periods.",
        "Late interest shall only apply after a 10-day written grace period.",
        [r"\blate\s+(?:fee|payment|charge)s?\b", r"\binterest\s+(?:at|of|on|will|shall)\b",
         r"\b(?:penalt(?:y|ies)|surcharge)\b", r"\b(?:overdue|delinquent)\s+(?:amount|payment|invoice)\b"]
    ),
    # 7. Privacy & Data Sharing
    RiskRule(
        "privacy_share", "Privacy & Data Sharing", "High", "Commercial Data Sharing or Sale",
        "Authorizes selling, commercial sharing, or disclosure of customer personal/usage data to third parties.",
        "Privacy breach, regulatory non-compliance (GDPR/DPDP), or commercial data exploitation.",
        "Restrict data processing strictly to service delivery and explicitly forbid data sale or monetization.",
        "Customer personal data shall not be sold, rented, or shared with third parties for marketing purposes.",
        [r"\b(?:sell|sells|sold|sale\s+of)\b[^.]{0,40}\b(?:data|information)\b",
         r"\b(?:share|shares|disclose\w*|transfer\w*)\b[^.]{0,60}\b(?:personal\s+data|customer\s+data|user\s+data)\b[^.]{0,60}\b(?:third[- ]part(?:y|ies)|affiliates?|advertis\w+|marketing)\b"]
    ),
    # 8. Confidentiality
    RiskRule(
        "confidentiality_protection", "Confidentiality", "Low", "Confidentiality Protection",
        "Protective covenant requiring proprietary information to be kept confidential and safe.",
        "Protects trade secrets and proprietary information.",
        "Ensure confidentiality covenants are mutual and apply reasonable care standards.",
        "Each party agrees to protect the other's confidential information with at least reasonable care.",
        [r"\bconfidential\w*\b", r"\bproprietary\s+information\b", r"\bprotect\w*\b[^.]{0,40}\bconfidential\b"]
    ),
    # 9. Liability Exclusions
    RiskRule(
        "liab_limit", "Liability Exclusions", "High", "Total Exclusion of Liability",
        "Disclaims all liability, warranties, or damages, leaving you with zero financial recourse.",
        "Bearing all losses yourself even for vendor negligence or system failure.",
        "Limit exclusions and carve out fraud, gross negligence, confidentiality breaches, and data protection.",
        "Neither party excludes liability for gross negligence, willful misconduct, or breach of confidentiality.",
        [r"\b(?:disclaim\w*|exclud\w*|waive\w*|release\w*)\b[^.]{0,60}\b(?:liabilit\w*|warrant\w*|damages|claims?)\b",
         r"\bno\s+liability\b", r"\bas[- ]is\b", r"\bwithout\s+(?:any\s+)?warrant\w*\b", r"\bat\s+own\s+risk\b"]
    ),
    # 10. Liability Caps
    RiskRule(
        "liab_cap_low", "Liability Caps", "High", "Unusually Low Liability Cap",
        "Caps liability to a nominal token amount (e.g. $50, $100, or 1 month fee) far below real risk exposure.",
        "Severe under-compensation if a major service failure or breach occurs.",
        "Negotiate a cap proportional to contract value (e.g. 12 months fees paid) or separate super-caps.",
        "The aggregate liability of either party shall be capped at the total fees paid in the preceding 12 months.",
        [r"\b(?:liability|damages)\b[^.]{0,80}\b(?:limited\s+to|capped\s+at|not\s+exceed)\b[^.]{0,30}\b(?:[1-9]\d{0,2}\s*(?:dollars|usd|rupees|inr|euros|eur|pounds|gbp)?|one\s+month|1\s+month)\b",
         r"\b(?:limited\s+to|shall\s+not\s+exceed)\s+(?:the\s+amount\s+of\s+)?(?:\$|USD\s*|INR\s*|Rs\.?\s*)[1-9]\d{0,2}\b"]
    ),
    RiskRule(
        "liab_cap", "Liability Caps", "Medium", "Monetary Liability Cap",
        "Liability is limited to a monetary ceiling, such as total fees paid under the contract.",
        "Ceiling on recoverable damages.",
        "Confirm the cap is mutual and matches realistic project impact.",
        "The aggregate liability of either party shall not exceed the fees paid under this agreement.",
        [r"\b(?:liabilit\w*|damages)\b[^.]{0,60}\b(?:limited\s+to|shall\s+not\s+exceed|capped|not\s+exceed)\b"]
    ),
    # 11. Unlimited / Broad Indemnification
    RiskRule(
        "liab_unlimited_indemnity", "Indemnification", "Critical", "Unlimited Indemnification",
        "Requires you to defend, indemnify, and hold harmless without limitation for broad third-party claims.",
        "Potentially catastrophic uncapped financial liability for third-party legal claims.",
        "Limit indemnity to direct damages, third-party IP claims, and proven willful misconduct.",
        "Each party agrees to indemnify the other solely against third-party claims arising from proven willful misconduct.",
        [r"\b(?:indemnif\w*|hold\s+harmless)\b[^.]{0,80}\b(?:without\s+limitation|unlimited|any\s+and\s+all\s+(?:claims|losses|damages|liabilities)|regardless\s+of\s+cause)\b",
         r"\b(?:defend|indemnify)\b[^.]{0,60}\b(?:against\s+(?:any|all)\s+claims\b[^.]{0,40}\b(?:howsoever\s+caused|arising\s+from\s+any\s+cause))\b"]
    ),
    RiskRule(
        "liab_indemnity", "Indemnification", "High", "Unilateral Indemnification Obligation",
        "Requires one party to indemnify and pay legal costs/damages for the other party.",
        "Financial liability for external legal actions or claims.",
        "Ensure indemnification is mutual and subject to prompt notice and defense rights.",
        "Each party shall promptly notify the other in writing of any claim subject to indemnification.",
        [r"\bindemnif\w*", r"\bhold\w*\s+(?:\w+\s+){0,2}harmless\b",
         r"\bresponsible\s+for\s+(?:all|any)\s+(?:losses|costs|damages|claims|expenses)\b"]
    ),
    # 12. Unilateral Amendments
    RiskRule(
        "change_unilateral", "Unilateral Amendments", "High", "Unilateral Terms Modification",
        "Allows provider to amend fees, service levels, or contractual terms at any time without your consent.",
        "Fees could increase or key protections could be removed without agreement.",
        "Require advance written notice (30+ days) and the right to terminate penalty-free if changes are adverse.",
        "Any amendment to these terms requires mutual written agreement signed by authorized representatives of both parties.",
        [r"\b(?:chang|modif|amend|updat|revis|alter|vary)\w*\b[^.]{0,80}\b(?:without\s+(?:any\s+)?(?:prior\s+)?(?:notice|consent)|sole\s+discretion|at\s+any\s+time)\b",
         r"\b(?:sole\s+discretion|at\s+any\s+time)\b[^.]{0,60}\b(?:chang|modif|amend|updat|revis|alter)\w*"]
    ),
    # 13. Service Availability & Downtime
    RiskRule(
        "service_downtime", "Service Availability", "High", "Downtime & Availability Disclaimer",
        "Disclaims all uptime guarantees, service availability, and liability for system outages.",
        "Loss of business continuity without service credits or contract remedies.",
        "Incorporate a formal SLA with minimum 99.9% availability and pro-rata fee credits for downtime.",
        "Provider warrants 99.9% monthly availability, with service fee credits provided for unscheduled outages.",
        [r"\b(?:no\s+guarantee|not\s+guarantee\w*|does\s+not\s+warrant)\b[^.]{0,60}\b(?:uninterrupted|error[- ]free|continuous|availability|uptime)\b",
         r"\b(?:downtime|outage|service\s+interruption)s?\b[^.]{0,60}\b(?:without\s+liability|no\s+liability|not\s+be\s+liable|sole\s+remedy)\b"],
        supports_negation=False
    ),
    # 14. Dispute Resolution
    RiskRule(
        "dispute_waiver", "Dispute Resolution", "High", "Jury Waiver / Mandatory Binding Arbitration",
        "Mandates private binding arbitration, waives jury trial, or prohibits participation in class actions.",
        "Surrendering court access, public appeal rights, or consumer protections.",
        "Consider local court jurisdiction or structured escalation (negotiation -> mediation -> litigation).",
        "Disputes shall first be submitted to good-faith executive negotiation and mediation prior to formal proceedings.",
        [r"\barbitrat\w*", r"\bclass[- ]action\b", r"\bwaive\w*\b[^.]{0,60}\b(?:jury|right\s+to\s+sue|court)\b",
         r"\bexclusive\s+jurisdiction\b"]
    ),
    # 15. Intellectual Property
    RiskRule(
        "ip_assignment", "Intellectual Property", "Medium", "IP Assignment or Ownership Rights",
        "Specifies ownership, transfer, or licensing of intellectual property, work product, or pre-existing IP.",
        "Risk of losing ownership of proprietary inventions or work product.",
        "Clarify that pre-existing background IP remains with original owner, and work product transfers upon full payment.",
        "Client shall own all deliverables upon full payment, while Consultant retains pre-existing tools and know-how.",
        [r"\bintellectual\s+property\b", r"\bwork\s+(?:made\s+for\s+hire|product)\b", r"\bassign\w*\b[^.]{0,40}\b(?:all\s+rights|ownership|ip|patents?|copyrights?)\b",
         r"\bownership\s+of\s+(?:deliverables|materials|software|code)\b"]
    )
]


def detect_clause_risks(clause: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Contextually detect risks for a single clause with negation and mutuality handling."""
    text = clause.get("text", "")
    section = clause.get("section", "")
    findings: List[Dict[str, Any]] = []

    # Check for protective exceptions across the clause
    has_protective_exception = bool(_PROTECTIVE_EXCEPTIONS.search(text))
    is_mutual = bool(_MUTUAL_RE.search(text))

    for rule in RULES:
        matched_span = None
        matched_text = ""

        for pattern in rule.patterns:
            m = pattern.search(text)
            if m:
                matched_span = [m.start(), m.end()]
                matched_text = m.group(0).strip()
                break

        if not matched_span:
            continue

        # Check negation
        negated = rule.supports_negation and is_clause_negated(text, matched_span[0])
        severity = rule.severity
        notes = []

        # Context adjustments
        if has_protective_exception and "liability" in rule.category.lower():
            negated = True
            notes.append("Protective exception (e.g. fraud or gross negligence) lowers liability risk.")

        if is_mutual:
            if severity == "Critical":
                severity = "High"
                notes.append("Bilateral clause applies equally to both parties.")
            elif severity == "High":
                severity = "Medium"
                notes.append("Mutual provision shared by both parties.")
            elif severity == "Medium":
                severity = "Low"
                notes.append("Standard bilateral commercial term.")

        if negated:
            severity = "Low"
            notes.append("Clause is negated or framed protectively.")

        confidence = 94 if matched_text else 80
        if is_mutual or negated:
            confidence = max(70, confidence - 10)

        findings.append({
            "rule_id": rule.rule_id,
            "category": rule.category,
            "level": severity,
            "title": rule.title,
            "explanation": f"Protective/Negated: {rule.explanation}" if negated else rule.explanation,
            "impact": rule.impact,
            "recommendation": None if (negated or severity == "Low") else rule.recommendation,
            "suggested_rephrase": None if (negated or severity == "Low") else rule.rephrase,
            "matched": matched_text,
            "span": matched_span,
            "spans": [matched_span],
            "confidence": confidence,
            "confidence_label": "Strong match" if confidence >= 75 else "Likely",
            "negated": negated,
            "is_mutual": is_mutual,
            "notes": notes,
            "section": section,
            "ref": clause.get("ref", "")
        })

    # Intra-clause Deduplication: Eliminate overlapping spans (keep highest severity)
    if len(findings) > 1:
        deduped = []
        findings.sort(key=lambda x: (-SEVERITY_LEVELS.get(x["level"], 1), -(x["span"][1] - x["span"][0]), -x["confidence"]))
        for f in findings:
            f_s, f_e = f["span"]
            overlap = False
            for d in deduped:
                d_s, d_e = d["span"]
                inter = max(0, min(f_e, d_e) - max(f_s, d_s))
                shorter = min(f_e - f_s, d_e - d_s)
                if shorter > 0 and (inter / float(shorter)) >= 0.45:
                    overlap = True
                    break
            if not overlap:
                deduped.append(f)
        findings = deduped

    return findings
