"""DocumentIQ 2.0 - Provider-Independent AI Service.

Connects to standard OpenAI-compatible API endpoints (or Ollama/Local) via standard
urllib without external hard dependencies. Gracefully falls back to offline mode.
"""
import json
import os
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

DEFAULT_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


class AIService:
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        self.api_base = os.environ.get("OPENAI_API_BASE", DEFAULT_OPENAI_URL).strip()
        self.model = os.environ.get("DOCUMENTIQ_AI_MODEL", DEFAULT_MODEL).strip()
        self.enabled = bool(self.api_key or "localhost" in self.api_base or "127.0.0.1" in self.api_base)

    def is_available(self) -> bool:
        return self.enabled

    def chat_completion(self, messages: list, temperature: float = 0.2, timeout: int = 15) -> Optional[str]:
        """Send chat completion request using standard library urllib."""
        if not self.is_available():
            return None

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DocumentIQ-ContractIntelligence/2.0"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature
        }

        try:
            req = urllib.request.Request(
                self.api_base,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["choices"][0]["message"]["content"].strip()
        except Exception:
            return None
        return None

    def analyze_clause_nuance(self, clause_text: str, context: str = "") -> Optional[Dict[str, Any]]:
        """Ask AI for deep second-opinion semantic understanding on a sensitive clause."""
        if not self.is_available():
            return None

        prompt = f"""You are DocumentIQ Legal Intelligence AI. Analyze this contract clause:
Clause: "{clause_text}"
Context/Section: "{context}"

Return a strict JSON object with:
{{
  "is_risk": true/false,
  "risk_level": "Low" | "Medium" | "High" | "Critical",
  "is_protective": true/false,
  "is_mutual": true/false,
  "explanation": "concise explanation",
  "recommendation": "actionable negotiation tip or null",
  "suggested_rephrase": "safer contract text or null"
}}
Return only valid JSON."""

        res = self.chat_completion([
            {"role": "system", "content": "You are a professional legal contract analysis assistant. Output strict JSON only."},
            {"role": "user", "content": prompt}
        ], temperature=0.1, timeout=8)

        if not res:
            return None
        try:
            # Clean markdown code blocks if any
            clean = res.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(clean)
        except Exception:
            return None

    def ask_contract_assistant(self, question: str, doc_context: str, contract_summary: dict,
                               parsed_clauses: list = None, payments: list = None, notices: list = None) -> str:
        """Ground conversational answers strictly in the analyzed document."""
        # 1. If live LLM API is available, use it
        if self.is_available():
            sys_prompt = (
                "You are the DocumentIQ AI Contract Assistant. You help users understand their contract. "
                "STRICT RULES:\n"
                "1. Answer strictly using the provided contract text and verified findings.\n"
                "2. If an amount, date, or condition is not mentioned in the contract, explicitly state that it is not present.\n"
                "3. Reference original clause numbers or sections when available.\n"
                "4. Never invent numbers, clauses, or obligations.\n"
                "5. Label draft counter-proposals as suggested drafting, not formal legal advice."
            )

            user_content = f"""Verified Contract Summary:
- Type: {contract_summary.get('type', 'Contract')}
- Attention Score: {contract_summary.get('score', 0)}/100 ({contract_summary.get('level', 'Low')})
- High Risks: {contract_summary.get('high', 0)}, Medium: {contract_summary.get('medium', 0)}, Low: {contract_summary.get('low', 0)}

Key Contract Excerpts & Structure:
{doc_context[:10000]}

User Question:
{question}"""

            ans = self.chat_completion([
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content}
            ], temperature=0.2, timeout=20)
            if ans:
                return ans

        # 2. Local Built-in Contract Intelligence Engine (Works 100% offline without API key)
        return self._local_contract_qna(question, doc_context, contract_summary, parsed_clauses or [], payments or [], notices or [])

    def _local_contract_qna(self, question: str, doc_context: str, summary: dict,
                            clauses: list, payments: list, notices: list) -> str:
        """High-precision local NLP contract question answering."""
        import re
        q = question.lower().strip()

        # A. Summary / Overview Question
        if any(w in q for w in ["summarize", "summary", "overview", "brief", "tell me about"]):
            score = summary.get("score", 0)
            lvl = summary.get("level", "Low")
            high = summary.get("high", 0) + summary.get("critical", 0)
            med = summary.get("medium", 0)
            takeaways = summary.get("takeaways", [])
            lines = [
                f"**Contract Assessment Summary:**",
                f"- **Overall Risk Rating:** {score}/100 ({lvl})",
                f"- **Critical/High Risks:** {high} | **Medium Risks:** {med}",
            ]
            if takeaways:
                lines.append("\n**Key Findings:**")
                for t in takeaways:
                    lines.append(f"• {t}")
            if payments:
                lines.append(f"\n**Financial Commitment:** Found {len(payments)} payment term(s), e.g. {payments[0].get('amount')} ({payments[0].get('kind', 'fee')}).")
            if notices:
                lines.append(f"**Notice Windows:** {len(notices)} deadline(s) identified, e.g. {notices[0].get('period')} for {notices[0].get('about')}.")
            return "\n".join(lines)

        # B. Biggest risks / Red flags
        if any(w in q for w in ["biggest risk", "main risk", "red flag", "danger", "risks"]):
            high_findings = []
            for c in clauses:
                for r in c.get("reasons", []):
                    if r.get("level") in ("Critical", "High"):
                        high_findings.append((c.get("ref", "§"), r.get("title"), r.get("explanation"), r.get("recommendation")))
            if not high_findings:
                return "✅ **No major red flags or critical risks detected.** The contract terms analyzed appear standard and balanced without unilateral penalty traps or unlimited liability."
            lines = ["⚠️ **Key High-Risk Provisions Identified:**"]
            for ref, title, exp, rec in high_findings[:5]:
                lines.append(f"\n• **Clause {ref} – {title}:**\n  {exp}")
                if rec:
                    lines.append(f"  *Negotiation Advice:* {rec}")
            return "\n".join(lines)

        # C. Early Termination / Penalty Question
        if any(w in q for w in ["terminate early", "early termination", "cancel early", "break the lease", "end early"]):
            penalty_clauses = [c for c in clauses if any(r.get("category") in ("Cancellation", "Early Termination Penalties") for r in c.get("reasons", []))]
            # Check if negated/protective
            negated_penalty = [c for c in penalty_clauses if any(r.get("negated") for r in c.get("reasons", []))]
            high_penalty = [c for c in penalty_clauses if any(r.get("level") == "High" and not r.get("negated") for r in c.get("reasons", []))]

            if negated_penalty:
                c = negated_penalty[0]
                return f"✅ **No Termination Penalty Applies:**\nAccording to clause **{c.get('ref', '§')}** (\"{c.get('text', '')}\"), no early termination penalty or charge is imposed on the parties upon ending the agreement."
            if high_penalty:
                c = high_penalty[0]
                r = next(x for x in c.get("reasons", []) if x.get("level") == "High")
                return f"⚠️ **Early Termination Financial Penalty Warning:**\nUnder clause **{c.get('ref', '§')}** (\"{c.get('text', '')}\"), an early exit fee or liquidated damages penalty is required. \n\n*Action:* {r.get('recommendation', 'Negotiate a cap or waiver for cause.')}"

            # Check notice periods
            term_notices = [n for n in notices if "terminat" in n.get("about", "").lower()]
            if term_notices:
                n = term_notices[0]
                return f"ℹ️ **Termination Notice Window:**\nYou may terminate by providing **{n.get('period')}** written notice as set forth in clause {n.get('ref', 'the agreement')} (\"{n.get('snippet', '')}\")."
            return "ℹ️ The contract allows termination with standard written notice; no explicit financial penalty clause was found."

        # D. Fee Increase / Price Change Question
        if any(w in q for w in ["increase", "raise fee", "price change", "fee change", "higher price"]):
            price_changes = [c for c in clauses if any(r.get("category") in ("Payment", "Changes") and "change" in r.get("title", "").lower() for r in c.get("reasons", []))]
            if price_changes:
                c = price_changes[0]
                return f"⚠️ **Price Modification Right Detected:**\nIn clause **{c.get('ref', '§')}** (\"{c.get('text', '')}\"), the other party reserves the right to increase or modify rates.\n\n*Negotiation Advice:* Require advance 30-day written notice and an annual cap on fee increases."
            return "✅ **No unilateral price increase rights were detected.** Fees cannot be altered without mutual agreement unless explicitly stated elsewhere."

        # E. Fees / Financial Obligations Question
        if any(w in q for w in ["fee", "pay", "monthly", "cost", "financial obligation", "how much", "rent", "deposit"]):
            if payments:
                lines = ["💰 **Identified Financial Obligations:**"]
                for p in payments[:6]:
                    freq = f" ({p['frequency']})" if p.get('frequency') else ""
                    cond = f" [Due: {p['condition']}]" if p.get('condition') else ""
                    lines.append(f"• **{p.get('amount')}{freq}** – {p.get('kind', 'Payment')}{cond} in clause {p.get('ref', '§')}")
                return "\n".join(lines)
            return "ℹ️ No specific numeric currency payment terms (e.g. $5,000, Rs. 25,000) were extracted from the document."

        # F. Renewal / Term Question
        if any(w in q for w in ["renew", "renewal", "evergreen", "duration", "how long", "term"]):
            renew_clauses = [c for c in clauses if any("renew" in r.get("category", "").lower() for r in c.get("reasons", []))]
            if renew_clauses:
                c = renew_clauses[0]
                r = c.get("reasons", [])[0]
                return f"🔄 **Renewal Terms:**\nClause **{c.get('ref', '§')}** states: \"{c.get('text', '')}\".\n\n*Classification:* {r.get('title')} ({r.get('level')} attention). {r.get('explanation')}"
            renew_notices = [n for n in notices if "renew" in n.get("about", "").lower()]
            if renew_notices:
                n = renew_notices[0]
                return f"🔄 **Renewal Notice:** A notice period of **{n.get('period')}** applies to non-renewal or opt-out in clause {n.get('ref', '§')}."
            return "ℹ️ No automatic multi-year rollover trap was detected. Standard expiration or mutual continuation applies."

        # G. Indemnification Question
        if any(w in q for w in ["indemnif", "indemnity", "hold harmless"]):
            indem = [c for c in clauses if any(r.get("category") == "Indemnification" or "indemni" in r.get("title", "").lower() for r in c.get("reasons", []))]
            if indem:
                c = indem[0]
                r = c.get("reasons", [])[0]
                is_unlimited = "unlimited" in r.get("title", "").lower()
                badge = "🚨 **UNLIMITED INDEMNIFICATION DETECTED**" if is_unlimited else "⚠️ **Indemnification Obligation:**"
                return f"{badge}\nClause **{c.get('ref', '§')}**: \"{c.get('text', '')}\"\n\n*Impact:* {r.get('impact', 'Potential liability for third-party legal claims.')}\n\n*Recommended Drafting:* {r.get('recommendation', 'Limit indemnity to third-party claims arising from proven breach.')}"
            return "✅ **No broad or unlimited indemnification clauses were detected in this agreement.**"

        # H. Liability Cap Question
        if any(w in q for w in ["liability cap", "cap", "liability limit", "maximum liability", "limit of liability"]):
            caps = [c for c in clauses if any("cap" in r.get("title", "").lower() or r.get("category") == "Liability Caps" for r in c.get("reasons", []))]
            if caps:
                c = caps[0]
                r = c.get("reasons", [])[0]
                return f"⚖️ **Liability Cap Provision:**\nClause **{c.get('ref', '§')}**: \"{c.get('text', '')}\"\n\n*Assessment:* {r.get('title')} ({r.get('level')} attention).\n{r.get('explanation')}"
            return "ℹ️ No explicit monetary liability cap clause was flagged in this document."

        # I. General Keyword / Contextual Match Fallback
        matched_clauses = []
        words = [w for w in re.findall(r"\w+", q) if len(w) > 3 and w not in ("what", "does", "have", "this", "that", "with", "from", "when", "about")]
        for c in clauses:
            t = c.get("text", "").lower()
            score = sum(1 for w in words if w in t)
            if score > 0:
                matched_clauses.append((score, c))
        matched_clauses.sort(key=lambda x: -x[0])

        if matched_clauses:
            top_c = matched_clauses[0][1]
            return f"📄 **Relevant Clause Found (Clause {top_c.get('ref', '§')}):**\n\"{top_c.get('text', '')}\"\n\n*Context:* Section '{top_c.get('section', 'General Provisions')}'. You can review specific risk flags associated with this provision in the findings list above."

        return (
            "I could not locate a specific clause addressing that in this contract. "
            "You can ask about:\n"
            "• \"Summarize this contract\"\n"
            "• \"What are the biggest risks?\"\n"
            "• \"What happens if I terminate early?\"\n"
            "• \"What are my payment obligations?\"\n"
            "• \"Does this contract contain unlimited indemnification?\""
        )


# Global singleton instance
ai_service = AIService()
