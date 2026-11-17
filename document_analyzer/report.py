"""PDF report generation with ReportLab."""
import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

DISCLAIMER = ("This report is informational. It is produced by predefined keyword and pattern rules and is not "
              "legal advice. A clause that is not flagged is not guaranteed to be safe.")
LEVEL_COLORS = {"High": "#c62f4a", "Medium": "#b06a00", "Low": "#1c7c7d"}
_REPLACE = {"\u20b9": "Rs. ", "\u2011": "-", "\u2212": "-", "\u00a0": " ", "\u2022": "-", "\u200b": ""}


def pdf_safe(text):
    """The built-in PDF fonts only cover Latin-1/Windows-1252, so map or drop other characters."""
    out = []
    for ch in str(text):
        ch = _REPLACE.get(ch, ch)
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            out.append("?")
    return "".join(out)


def _t(text):
    return escape(pdf_safe(text))


def _where(item):
    bits = []
    if item.get("page"):
        bits.append(f"page {item['page']}")
    if item.get("ref"):
        bits.append(f"clause {item['ref']}")
    return ", ".join(bits) if bits else "-"


STYLES = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=4),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=14, spaceAfter=6),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#55607a")),
    "quote": ParagraphStyle("quote", fontName="Times-Italic", fontSize=10, leading=13.5, leftIndent=10, spaceBefore=2, spaceAfter=3),
    "reason": ParagraphStyle("reason", fontName="Helvetica", fontSize=9, leading=12, leftIndent=10, spaceAfter=2),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=11),
    "head": ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=colors.white),
}


def _table(headers, rows, widths):
    data = [[Paragraph(_t(h), STYLES["head"]) for h in headers]]
    data += [[Paragraph(_t(c), STYLES["cell"]) for c in row] for row in rows]
    table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b3a8c")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f8")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d5d9e4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#55607a"))
    canvas.drawString(18 * mm, 10 * mm, "Informational only. Not legal advice.")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_report(meta, result):
    """meta: dict(filename, created_at). result: analysis dict. Returns PDF bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title="Document Risk and Obligation Analysis", author="Document Analyzer")
    s, S = result["summary"], STYLES
    story = [Paragraph("Document Risk &amp; Obligation Analysis", S["title"]),
             Paragraph(_t(DISCLAIMER), S["small"]), Spacer(1, 8)]

    pages = result["page_count"] if result["page_count"] else "-"
    info = [["File", meta["filename"]], ["Analysed on", meta["created_at"]],
            ["Pages", str(pages)], ["Clauses checked", str(result["clause_count"])],
            ["Overall attention", f"{s['level']} (score {s['score']} out of 100)"],
            ["Flags found", f"High: {s['high']}   Medium: {s['medium']}   Low: {s['low']}"]]
    t = Table([[Paragraph(f"<b>{_t(a)}</b>", S["cell"]), Paragraph(_t(b), S["cell"])] for a, b in info],
              colWidths=[38 * mm, 136 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d5d9e4")),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f8")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(t)
    if result.get("truncated"):
        story += [Spacer(1, 4), Paragraph("Only the first clauses of this long document were analysed.", S["small"])]

    story.append(Paragraph("Extracted obligations", S["h2"]))
    story.append(Paragraph("Payments and fees", S["body"]))
    story.append(Spacer(1, 3))
    if result["payments"]:
        rows = [[p["amount"], p["kind"], " ".join(x for x in (p["frequency"], p["condition"]) if x) or "-",
                 _where(p), p["text"]] for p in result["payments"]]
        story.append(_table(["Amount", "Type", "Timing", "Where", "Sentence"], rows, [22, 24, 38, 24, 66]))
    else:
        story.append(Paragraph("No payment amounts were found.", S["small"]))
    story += [Spacer(1, 8), Paragraph("Notice periods", S["body"]), Spacer(1, 3)]
    if result["notices"]:
        rows = [[n["period"], n["about"], _where(n), n["text"]] for n in result["notices"]]
        story.append(_table(["Period", "About", "Where", "Sentence"], rows, [22, 36, 24, 92]))
    else:
        story.append(Paragraph("No notice periods were found.", S["small"]))

    story.append(Paragraph("Flagged clauses", S["h2"]))
    if not result["flagged"]:
        story.append(Paragraph("No clauses matched the detection rules.", S["small"]))
    for c in result["flagged"]:
        color = LEVEL_COLORS[c["level"]]
        head = (f'<font color="{color}"><b>{_t(c["level"])}</b></font>&nbsp;&nbsp;'
                f'<font color="#55607a">{_t(_where(c))}</font>')
        if c.get("section"):
            head += f'&nbsp;&nbsp;<font color="#55607a">in {_t(c["section"])}</font>'
        block = [Paragraph(head, S["body"]), Paragraph(_t(c["text"]), S["quote"])]
        for r in c["reasons"]:
            rc = LEVEL_COLORS[r["level"]]
            how = {"meaning": "Found by meaning", "both": "Found by wording and meaning"}.get(r.get("detection"), "Matched")
            sure = f' ({_t(r["confidence_label"])}, {r["confidence"]}%)' if "confidence" in r else ""
            block.append(Paragraph(
                f'<font color="{rc}"><b>{_t(r["category"])}, {_t(r["level"])}:</b></font> '
                f'<b>{_t(r["title"])}.</b> {how}: "{_t(r["matched"])}"{sure}. {_t(r["explanation"])}', S["reason"]))
        block.append(Spacer(1, 8))
        story.append(KeepTogether(block))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()

def build_portfolio_report(company_name, analyses_list, summary_stats):
    """Generates an executive multi-contract portfolio audit PDF."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title="Portfolio Risk Audit - DocumentIQ", author="DocumentIQ")
    S = STYLES
    story = [
        Paragraph("DocumentIQ - Portfolio Risk &amp; Compliance Audit", S["title"]),
        Paragraph(f"Organization: {_t(company_name or 'Enterprise Portfolio')} | Generated on: {_t(summary_stats['date'])}", S["small"]),
        Paragraph(_t(DISCLAIMER), S["small"]),
        Spacer(1, 10),
        Paragraph("Portfolio Risk Summary", S["h2"]),
    ]

    overview_data = [
        ["Total Contracts Analyzed", str(summary_stats["total_docs"])],
        ["Average Portfolio Risk Score", f"{summary_stats['avg_score']} / 100 ({summary_stats['avg_level']})"],
        ["Total High Liability Flags", str(summary_stats["total_high"])],
        ["Total Medium Exposure Flags", str(summary_stats["total_medium"])],
        ["Total Low Risk Indicators", str(summary_stats["total_low"])],
        ["Total Tracked Obligations", str(summary_stats["total_obligations"])],
    ]
    t = Table([[Paragraph(f"<b>{_t(a)}</b>", S["cell"]), Paragraph(_t(b), S["cell"])] for a, b in overview_data],
              colWidths=[55 * mm, 119 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d5d9e4")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Contract Repository Breakdown", S["h2"]))
    headers = ["Contract Filename", "Analyzed Date", "Score", "Risk Level", "High Flags"]
    rows = []
    for a in analyses_list:
        rows.append([
            a.get("filename", "-"),
            a.get("created_at", "-")[:10],
            f"{a.get('score', 0)}/100",
            a.get("level", "-"),
            str(a.get("high_count", 0)),
        ])
    if rows:
        story.append(_table(headers, rows, [65, 30, 22, 28, 29]))
    else:
        story.append(Paragraph("No contracts in portfolio repository.", S["body"]))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
