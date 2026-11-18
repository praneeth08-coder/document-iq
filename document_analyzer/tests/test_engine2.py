"""DocumentIQ 2.0 - Complete Regression Test Suite.

Automated regression testing across:
A. Safe Consultancy Agreement (Low risk)
B. Moderate-Risk Consulting Agreement (Moderate risk)
C. High-Risk Cloud/SaaS Services Agreement (Critical/High risk)
D. Technical Project Documentation (Non-contractual, 0 risk score)
E. Granular Clause Tests (Negation, Mutuality, Low Cap, SLA, Early Penalty)
"""
import unittest
import sys
import os

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base)

from engine.core import analyze_document_pipeline
from engine.classifier import classify_document
from engine.risk_detector import detect_clause_risks


class DocumentIQ2RegressionTests(unittest.TestCase):

    def test_safe_consultancy_agreement(self):
        """A. Safe balanced consultancy agreement must score genuinely Low (<= 15) with 0 High/Critical risks."""
        text = """
CONSULTANCY AGREEMENT
1. Services and Fees. Consultant shall provide engineering consulting services. Client shall pay a monthly consultancy fee of $5,000 within thirty (30) days of receiving an invoice.
2. Term and Termination. Either party may terminate this Agreement by providing thirty (30) days written notice to the other party. No termination penalty shall apply to either party upon termination.
3. Confidentiality. Confidential information shall not be disclosed to unauthorized third parties without written consent. Each party agrees to protect confidential information with reasonable care.
4. Limitation of Liability. The total aggregate liability of either party shall not exceed the total consultancy fees paid under this agreement. Nothing in this clause excludes liability for fraud or wilful misconduct.
        """
        res = analyze_document_pipeline([(1, text)], filename="safe_contract.docx")
        s = res["summary"]
        self.assertEqual(res["document_type"], "Consultancy Agreement")
        self.assertTrue(res["is_contract"])
        self.assertEqual(s["critical"], 0)
        self.assertEqual(s["high"], 0)
        self.assertLessEqual(s["score"], 15)
        self.assertEqual(s["level"], "Low")

    def test_moderate_risk_agreement(self):
        """B. Agreement with standard operational provisions (notice, late fees, deposits) receives Moderate score."""
        text = """
MASTER SERVICES AGREEMENT
1. Scope of Work. Consultant shall provide development services as described in individual Work Orders.
2. Invoicing. Client shall pay invoices within 45 days. Late payments shall bear interest at 1.5% per month.
3. Monetary Liability Cap. The total aggregate liability of either party arising out of or related to this Agreement shall be limited to the fees paid by Client in the preceding 12 months.
4. Termination Notice. Either party may terminate this Agreement without cause upon providing 60 days advance written notice.
5. Automatic Renewal. This Agreement shall automatically renew for successive one-year terms unless either party provides written notice of non-renewal at least 30 days prior to expiration.
        """
        res = analyze_document_pipeline([(1, text)], filename="services_sow.docx")
        s = res["summary"]
        self.assertTrue(res["is_contract"])
        # Should have at most 1 high item (auto-renewal) and medium items
        self.assertLessEqual(s["critical"], 0)
        self.assertIn(s["level"], ("Moderate", "Low"))
        self.assertGreaterEqual(s["score"], 20)
        self.assertLessEqual(s["score"], 60)

    def test_high_risk_saas_agreement(self):
        """C. Predatory SaaS agreement with unlimited indemnity and unilateral rights must score High/Critical (>= 80)."""
        text = """
ENTERPRISE SAAS MASTER AGREEMENT
1. Unlimited Indemnification. Customer agrees to defend, indemnify, and hold Provider harmless without limitation against any and all claims, damages, liabilities, and expenses howsoever caused.
2. Data Sharing. Provider reserves the right to sell, transfer, share, and disclose customer personal data and usage data to third-party affiliates and marketing partners.
3. Low Liability Cap. Notwithstanding anything to the contrary, Provider's aggregate liability under this agreement shall be strictly limited to $50.
4. Unilateral Amendments. Provider may modify, amend, alter, or update terms, conditions, or fees at any time in its sole discretion without prior notice.
5. Service Availability. Provider does not guarantee uninterrupted, continuous, or error-free availability or uptime, and disclaims all liability for service outages and downtime without liability or service credits.
6. Early Termination Penalty. In the event of early termination, Customer must immediately pay an early termination fee equal to the remaining balance of the full 3-year term as liquidated damages.
        """
        res = analyze_document_pipeline([(1, text)], filename="predatory_terms.pdf")
        s = res["summary"]
        self.assertTrue(res["is_contract"])
        self.assertGreaterEqual(s["critical"] + s["high"], 4)
        self.assertGreaterEqual(s["score"], 85)
        self.assertIn(s["level"], ("High", "Critical"))

    def test_technical_documentation_not_treated_as_contract(self):
        """D. Technical document mentioning liability, docker, or endpoints must NOT receive contract risk penalties."""
        text = """
SYSTEM ARCHITECTURE SPECIFICATION
Microservices API & Docker Deployment Documentation
1. Architecture Overview. The application uses a containerized Docker deployment on Kubernetes with an API Gateway.
2. Endpoint Specifications. POST /api/v1/auth/token generates JWT sessions with automatic renewal every 24 hours.
3. Error Handling and Liability. Services must return HTTP 500 when downstream database timeout occurs without crashing the worker pool.
4. Data Pipeline. Ingestion pipeline streams events via Apache Kafka with at-least-once delivery guarantees.
        """
        res = analyze_document_pipeline([(1, text)], filename="architecture_spec.txt")
        self.assertFalse(res["is_contract"])
        self.assertEqual(res["document_type"], "Technical Documentation")
        self.assertEqual(res["summary"]["score"], 0)
        self.assertEqual(res["summary"]["level"], "Low")

    def test_granular_negation_and_exceptions(self):
        """E. Test individual nuanced clauses."""
        # 1. No termination penalty
        f1 = detect_clause_risks({"text": "No termination penalty shall apply to either party upon termination."})
        self.assertTrue(all(r["level"] == "Low" and r["negated"] for r in f1))

        # 2. Fraud exception
        f2 = detect_clause_risks({"text": "Nothing in this agreement excludes liability for fraud or wilful misconduct."})
        self.assertTrue(all(r["level"] == "Low" for r in f2))

        # 3. Bilateral 30-day notice
        f3 = detect_clause_risks({"text": "Either party may terminate this agreement upon thirty (30) days written notice."})
        self.assertTrue(all(r["level"] == "Low" and r["is_mutual"] for r in f3))

        # 4. Low cap ($50)
        f4 = detect_clause_risks({"text": "Provider's aggregate liability under this agreement shall be strictly limited to $50."})
        self.assertTrue(any(r["title"] == "Unusually Low Liability Cap" and r["level"] == "High" for r in f4))

        # 5. Service downtime disclaimer
        f5 = detect_clause_risks({"text": "Provider does not warrant uninterrupted uptime and disclaims all liability for service downtime."})
        self.assertTrue(any(r["category"] == "Service Availability" and r["level"] == "High" for r in f5))


if __name__ == "__main__":
    unittest.main()
