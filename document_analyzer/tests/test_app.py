"""Run with:  python -m unittest discover -s tests -v"""
import io
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_tmp = tempfile.mkdtemp()
os.environ["ANALYZER_DB"] = os.path.join(_tmp, "test.db")

import analyzer as A  # noqa: E402
import app as app_module  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")


def sample(name):
    with open(os.path.join(SAMPLES, name), "rb") as fh:
        return fh.read()


def make_docx(paragraphs, table_rows=()):
    from docx import Document
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    if table_rows:
        t = doc.add_table(rows=0, cols=2)
        for a, b in table_rows:
            cells = t.add_row().cells
            cells[0].text, cells[1].text = a, b
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_pdf(pages, blank=False):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for lines in pages:
        if not blank:
            y = 800
            for line in lines:
                c.drawString(50, y, line)
                y -= 16
        c.showPage()
    c.save()
    return buf.getvalue()


def levels(text):
    return {r["category"]: (r["level"], r["negated"]) for r in A.analyze_clause(text)}


class RuleTests(unittest.TestCase):
    def test_auto_renewal_flagged(self):
        self.assertEqual(levels("The plan renews automatically every year.")["Renewal"], ("High", False))

    def test_negated_renewal_is_low(self):
        self.assertEqual(levels("This agreement does not renew automatically.")["Renewal"], ("Low", True))
        self.assertEqual(levels("There is no automatic renewal.")["Renewal"], ("Low", True))

    def test_negation_in_other_part_of_sentence_ignored(self):
        t = "You do not need to do anything; the agreement renews automatically."
        self.assertEqual(levels(t)["Renewal"], ("High", False))

    def test_no_later_than_is_not_negation(self):
        t = "Notify us no later than 30 days before it renews automatically."
        self.assertEqual(levels(t)["Renewal"], ("High", False))

    def test_refund_rules(self):
        self.assertEqual(levels("The fee is non-refundable.")["Refund"][0], "High")
        self.assertEqual(levels("Fees will not be refunded.")["Refund"][0], "High")
        self.assertEqual(levels("You may request a full refund within 14 days.")["Refund"][0], "Low")

    def test_fee_negation(self):
        self.assertEqual(levels("No early termination fee applies.")["Cancellation"], ("Low", True))
        self.assertEqual(levels("An early termination fee applies.")["Cancellation"], ("High", False))

    def test_privacy_and_liability(self):
        self.assertEqual(levels("We may share your personal information with third parties.")["Privacy"][0], "High")
        self.assertEqual(levels("We do not sell your personal information.")["Privacy"], ("Low", True))
        self.assertEqual(levels("The company shall not be liable for any loss.")["Liability"][0], "High")
        self.assertEqual(levels("You agree to indemnify the company.")["Liability"][0], "High")

    def test_plain_sentence_not_flagged(self):
        self.assertEqual(A.analyze_clause("The office is located on the second floor of the building."), [])

    def test_score_levels(self):
        self.assertEqual(A.score_from_counts(0, 0, 0), (0, "Low"))
        self.assertEqual(A.score_from_counts(5, 0, 0), (60, "High"))
        self.assertEqual(A.score_from_counts(10, 10, 10)[0], 100)


class SplitTests(unittest.TestCase):
    def test_abbreviations_and_decimals_do_not_split(self):
        s = A._sentences("The fee is Rs. 5,000 or 5.5 percent, e.g. monthly. Payment is due soon.")
        self.assertEqual(len(s), 2)

    def test_headings_become_sections(self):
        text = "1. Renewal\nThis agreement renews automatically each year.\n\n2. Fees\nThe tenant shall pay Rs. 500."
        clauses, _ = A.build_clauses([(None, text)])
        self.assertEqual([c["section"] for c in clauses], ["1. Renewal", "2. Fees"])

    def test_wrapped_lines_are_joined(self):
        text = "The tenant shall give thirty days\nwritten notice before leaving the\nproperty."
        clauses, _ = A.build_clauses([(None, text)])
        self.assertEqual(len(clauses), 1)

    def test_short_real_clause_not_treated_as_heading(self):
        clauses, _ = A.build_clauses([(None, "No refunds.\n\nThe office opens at nine each morning.")])
        self.assertTrue(any(c["text"].startswith("No refunds") for c in clauses))


class ObligationTests(unittest.TestCase):
    def test_payments_and_notices(self):
        text = ("The tenant shall pay rent of Rs. 25,000 per month, due on or before the 5th of each month. "
                "Either party must give sixty (60) days' written notice. "
                "The lease is for twelve-month terms and costs two months' rent as a deposit.")
        clauses, _ = A.build_clauses([(None, text)])
        pay, notice = A.extract_obligations(clauses)
        self.assertEqual(pay[0]["amount"], "Rs. 25,000")
        self.assertEqual(pay[0]["frequency"], "Monthly")
        self.assertIn("5th", pay[0]["condition"])
        self.assertEqual([n["period"] for n in notice], ["60 days"])

    def test_dollar_and_percent(self):
        clauses, _ = A.build_clauses([(None, "A late fee of $25 plus 1.5% interest applies to overdue invoices.")])
        pay, _ = A.extract_obligations(clauses)
        self.assertEqual(sorted(p["amount"] for p in pay), ["$25", "1.5%"])


class CompareTests(unittest.TestCase):
    def test_sample_versions(self):
        a, _ = A.build_clauses(A.extract_pages("txt", sample("rental_agreement_v1.txt")))
        b, _ = A.build_clauses(A.extract_pages("txt", sample("rental_agreement_v2.txt")))
        d = A.compare_documents(a, b)
        self.assertEqual(len(d["modified"]), 3)
        self.assertEqual(len(d["added"]), 2)
        self.assertEqual(len(d["removed"]), 2)
        self.assertGreater(d["unchanged"], 5)

    def test_identical(self):
        a, _ = A.build_clauses(A.extract_pages("txt", sample("rental_agreement_v1.txt")))
        d = A.compare_documents(a, a)
        self.assertFalse(d["modified"] or d["added"] or d["removed"])


class ExtractionTests(unittest.TestCase):
    def test_docx_with_table(self):
        data = make_docx(["The subscription renews automatically each month."], [("Monthly fee", "$20")])
        pages = A.extract_pages("docx", data)
        self.assertIn("renews automatically", pages[0][1])
        self.assertIn("Monthly fee | $20", pages[0][1])

    def test_pdf_pages(self):
        data = make_pdf([["Page one: the plan renews automatically every year."],
                         ["Page two: fees are non-refundable after thirty days."]])
        pages = A.extract_pages("pdf", data)
        self.assertEqual([p for p, _ in pages], [1, 2])
        result = A.analyze_document(pages)
        self.assertEqual(result["page_count"], 2)
        self.assertEqual({f["page"] for f in result["flagged"]}, {1, 2})

    def test_scanned_pdf_message(self):
        with self.assertRaises(A.ExtractionError) as ctx:
            A.extract_pages("pdf", make_pdf([[]], blank=True))
        self.assertIn("scanned", str(ctx.exception))

    def test_bad_files(self):
        for ext, data in [("pdf", b"not a pdf"), ("docx", b"not a zip"), ("txt", b"\x00\x01\x02" * 50)]:
            with self.assertRaises(A.ExtractionError):
                A.extract_pages(ext, data)

    def test_txt_encodings(self):
        text = "The fee of \u20b9500 is non-refundable and payable monthly by the tenant."
        for enc in ("utf-8", "utf-8-sig", "utf-16"):
            pages = A.extract_pages("txt", text.encode(enc))
            self.assertIn("non-refundable", pages[0][1])
        pages = A.extract_pages("txt", "Caf\xe9 fee is non-refundable and due every month.".encode("cp1252"))
        self.assertIn("Caf", pages[0][1])


class WebTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.c = app_module.app.test_client()
        self.token = self._token(self.c)

    def _token(self, client):
        page = client.get("/").get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def upload(self, name, data, token=None, client=None):
        client = client or self.c
        return client.post("/analyze", data={"csrf_token": token or self.token,
                                             "document": (io.BytesIO(data), name)},
                           content_type="multipart/form-data")

    def test_pages_render(self):
        for url in ("/", "/compare", "/history"):
            self.assertEqual(self.c.get(url).status_code, 200)
        self.assertEqual(self.c.get("/nope").status_code, 404)

    def test_full_flow(self):
        r = self.upload("lease.txt", sample("rental_agreement_v1.txt"))
        self.assertEqual(r.status_code, 302)
        page = self.c.get(r.headers["Location"])
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Automatic renewal", html)
        self.assertIn("Rs. 25,000", html)
        self.assertIn("60 days", html)
        self.assertIn("<mark", html)
        aid = int(r.headers["Location"].rsplit("/", 1)[1])
        pdf = self.c.get(f"/analysis/{aid}/report.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertIn("lease.txt", self.c.get("/history").get_data(as_text=True))
        d = self.c.post(f"/analysis/{aid}/delete", data={"csrf_token": self.token})
        self.assertEqual(d.status_code, 302)
        self.assertEqual(self.c.get(f"/analysis/{aid}").status_code, 404)

    def test_pdf_and_docx_upload(self):
        pdf = make_pdf([["The plan renews automatically every year unless cancelled."]])
        docx = make_docx(["Fees are non-refundable.  The tenant shall pay Rs. 500 monthly."])
        self.assertEqual(self.upload("a.pdf", pdf).status_code, 302)
        self.assertEqual(self.upload("b.docx", docx).status_code, 302)

    def test_validation_errors(self):
        self.assertEqual(self.upload("x.exe", b"MZ" * 40).status_code, 400)
        self.assertEqual(self.upload("empty.txt", b"").status_code, 400)
        r = self.upload("scan.pdf", make_pdf([[]], blank=True))
        self.assertEqual(r.status_code, 400)
        self.assertIn("scanned", r.get_data(as_text=True))
        r = self.c.post("/analyze", data={"csrf_token": self.token}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_csrf_required(self):
        r = self.c.post("/analyze", data={"document": (io.BytesIO(b"hello world text"), "a.txt")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_history_is_private(self):
        r = self.upload("private.txt", sample("rental_agreement_v1.txt"))
        url = r.headers["Location"]
        other = app_module.app.test_client()
        self._token(other)
        self.assertEqual(other.get(url).status_code, 404)
        self.assertEqual(other.get(url + "/report.pdf").status_code, 404)
        self.assertNotIn("private.txt", other.get("/history").get_data(as_text=True))

    def test_html_is_escaped(self):
        r = self.upload("<script>alert(1)</script>.txt",
                        b"The tenant shall <b>pay</b> Rs. 500 and fees are non-refundable.")
        html = self.c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<b>pay</b>", html)

    def test_compare_flow(self):
        r = self.c.post("/compare", data={
            "csrf_token": self.token,
            "old_doc": (io.BytesIO(sample("rental_agreement_v1.txt")), "v1.txt"),
            "new_doc": (io.BytesIO(sample("rental_agreement_v2.txt")), "v2.txt")},
            content_type="multipart/form-data")
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("<ins>", html)
        self.assertIn("<del>", html)
        self.assertIn("Modified clauses", html)
        r = self.c.post("/compare", data={"csrf_token": self.token,
                                          "old_doc": (io.BytesIO(b"hello there friend"), "v1.txt")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_report_handles_unicode(self):
        import report
        pages = A.extract_pages("txt", "The tenant shall pay \u20b95,000 monthly \u2013 fees are non-refundable \u4e2d\u6587.".encode())
        pdf = report.build_report({"filename": "\u20b9 \u4e2d\u6587 & <x>.txt", "created_at": "now"}, A.analyze_document(pages))
        self.assertTrue(pdf.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
