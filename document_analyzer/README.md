# Document Risk & Obligation Analyzer

A rule-based Flask web app that reads PDF, DOCX and TXT documents, flags clauses that deserve attention,
extracts payment amounts and notice periods, compares two versions, keeps a history and exports PDF reports.
No machine learning and no paid APIs: every result comes from a rule you can read in `analyzer.py`.

> Informational assistance only. It does not replace professional legal advice, and a clause that is
> not flagged is not guaranteed to be safe.

## Run it

```bash
python -m venv venv
venv\Scripts\activate            # Windows      (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. Try the files in `samples/` (compare `rental_agreement_v1.txt` with `..._v2.txt`).

Optional settings: `PORT=8000`, `SECRET_KEY=...`, `FLASK_DEBUG=1`, `ANALYZER_DB=path/to/file.db`.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Features (all nine)

| Feature | Where |
|---|---|
| Document upload (PDF, DOCX, TXT) | `app.py` `/analyze`, drag and drop in `static/js/app.js` |
| Text extraction with PDF page references | `analyzer.extract_pages` (PyMuPDF, python-docx) |
| Clause detection | `analyzer.build_clauses`, `analyzer.RULES` |
| High / Medium / Low levels and overall score | `analyzer.analyze_clause`, `analyzer.score_from_counts` |
| Explanation, reason, page reference, highlighted original text | `templates/result.html` |
| Payment amounts, timing and notice periods | `analyzer.extract_obligations` |
| Version comparison (added / removed / modified) | `analyzer.compare_documents` (difflib) |
| History in SQLite, open and delete | `/history`, table `analyses` |
| PDF report | `report.py` (ReportLab), `/analysis/<id>/report.pdf` |

## How the analysis works

1. **Extract** text. PDFs keep page numbers. Scanned PDFs (no text) give a clear message because OCR is out of scope.
2. **Split** into clauses: headings and numbering become section labels, wrapped lines are joined, then
   sentences are split with protection for `Rs.`, `No.`, `e.g.` and decimals like `5.5`.
3. **Match rules** (regex). Each category (Renewal, Cancellation, Refund, Payment, Privacy, Liability,
   Disputes, Changes) reports its most severe matching rule per clause.
4. **Negation handling**: for rules where "not" reverses the meaning (for example "does not renew automatically"),
   the match is lowered to Low and labelled as likely favourable. Negation is only looked for just before the phrase
   and within the same part of the sentence.
5. **Score** = 12 per High + 5 per Medium + 1 per Low, capped at 100. 60 or more is High, 30 to 59 is Medium.
6. **Compare**: identical clauses are matched first, then similar ones (word-level similarity of 60% or more) are
   marked modified with a word diff, and the rest are added or removed.

To add a rule, copy a `_add(...)` call in `analyzer.py`. Order inside a category matters: most severe first.

## Privacy and security choices

- Uploaded files are read in memory and never written to disk. Only analysis results are stored.
- Each browser gets a private owner id (signed cookie), so history is not shared between people.
- Forms use CSRF tokens, uploads are type and size checked (10 MB each), and all text is HTML-escaped.
- No login system, as per the project scope. Run it locally or behind your own access control.

## Known limits

Rule matching can miss unusual wording and can flag harmless sentences. Tables in PDFs may extract in odd order.
Only English wording is covered by the rules. Use the output as a checklist, not as a verdict.

## Project layout

```
app.py          routes, SQLite, upload handling, CSRF
analyzer.py     extraction, clause splitting, rules, obligations, comparison
report.py       PDF report
templates/      pages          static/   css and js
samples/        two rental agreement versions      tests/   unit and route tests
```
