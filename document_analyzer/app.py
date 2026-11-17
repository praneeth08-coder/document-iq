"""Document Risk & Obligation Analyzer - DocumentIQ Enterprise Flask application."""
import functools
import io
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

from flask import (Flask, Response, abort, flash, g, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

import analyzer
import report

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)
DB_PATH = os.environ.get("ANALYZER_DB", os.path.join(INSTANCE_DIR, "analyzer.db"))

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "txt"}
MAX_FILE_BYTES = 10 * 1024 * 1024


def _secret_key():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    path = os.path.join(INSTANCE_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(path) as fh:
        return fh.read().strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_secret_key(),
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=60),
)


class UserError(Exception):
    """A problem the user can fix, shown on the page."""


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    company TEXT,
    created_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS generated_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    report_type TEXT NOT NULL,
    format TEXT NOT NULL,
    created_at TEXT NOT NULL,
    doc_ids_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    doc_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    page_count INTEGER,
    clause_count INTEGER NOT NULL,
    high_count INTEGER NOT NULL,
    medium_count INTEGER NOT NULL,
    low_count INTEGER NOT NULL,
    score INTEGER NOT NULL,
    level TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_owner ON analyses(owner, id DESC);
"""


def init_db():
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


init_db()


# --------------------------------------------------------------------------
# Sessions, CSRF and Auth Guard
# --------------------------------------------------------------------------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if not session.get("user_id"):
            flash("Please sign in to access the DocumentIQ application.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(**kwargs)
    return wrapped_view


@app.before_request
def prepare_request():
    session.permanent = True
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent.encode(), csrf_token().encode()):
            abort(400, description="Your session expired. Go back, refresh the page and try again.")


def get_user_record():
    if session.get("user_id"):
        return get_db().execute("SELECT id, email, full_name, company FROM users WHERE id=?", 
                                (session["user_id"],)).fetchone()
    return None

@app.context_processor
def inject_user():
    user = None
    if session.get("user_id"):
        user = get_db().execute("SELECT id, email, full_name, company FROM users WHERE id=?", 
                                (session["user_id"],)).fetchone()
    return {"current_user": user}


@app.template_filter("slug")
def slug(value):
    return str(value).lower().replace(" ", "-")


def read_upload(field):
    """Read one uploaded file into memory. Returns (display_name, extension, bytes)."""
    f = request.files.get(field)
    if f is None or not f.filename:
        raise UserError("Choose a file first.")
    name = os.path.basename(f.filename.replace("\\", "/")).strip()[:120] or "document"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise UserError(analyzer.SUPPORTED_MESSAGE)
    data = f.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise UserError("That file is larger than 10 MB. Upload a smaller file.")
    if not data:
        raise UserError("That file is empty.")
    return name, ext, data


def current_owner():
    if session.get("user_id"):
        return f"user_{session['user_id']}"
    if "guest_owner" not in session:
        session["guest_owner"] = secrets.token_hex(16)
    return session["guest_owner"]


def recent_rows(limit=5):
    return get_db().execute(
        "SELECT id, filename, created_at, score, level FROM analyses WHERE owner=? ORDER BY id DESC LIMIT ?",
        (current_owner(), limit)).fetchall()


def get_analysis_or_404(analysis_id):
    row = get_db().execute("SELECT * FROM analyses WHERE id=? AND owner=?",
                           (analysis_id, current_owner())).fetchone()
    if row is None:
        abort(404)
    return row


# --------------------------------------------------------------------------
# Public & Authentication Routes
# --------------------------------------------------------------------------
@app.get("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("public_home.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            session["user_name"] = user["full_name"]
            next_url = request.args.get("next")
            return redirect(next_url or url_for("dashboard"))
        error = "Invalid email or password. Please check your credentials."
    return render_template("auth/login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        company = request.form.get("company", "").strip()
        password = request.form.get("password", "")
        if not email or not password or not full_name:
            error = "Please fill in all required fields."
        elif len(password) < 6:
            error = "Password must be at least 6 characters long."
        else:
            db = get_db()
            existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                error = "An account with that email already exists. Sign in instead."
            else:
                p_hash = generate_password_hash(password)
                cur = db.execute(
                    "INSERT INTO users (email, full_name, password_hash, company, created_at) VALUES (?,?,?,?,?)",
                    (email, full_name, p_hash, company, datetime.now().strftime("%Y-%m-%d %H:%M"))
                )
                db.commit()
                session["user_id"] = cur.lastrowid
                session["user_email"] = email
                session["user_name"] = full_name
                flash("Account created successfully! Welcome to DocumentIQ.", "success")
                return redirect(url_for("dashboard"))
    return render_template("auth/register.html", error=error)


@app.post("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("user_email", None)
    session.pop("user_name", None)
    flash("You have been signed out successfully.", "info")
    return redirect(url_for("home"))


# --------------------------------------------------------------------------
# Authenticated Application Routes
# --------------------------------------------------------------------------
@app.get("/dashboard")
@login_required
def dashboard():
    owner = current_owner()
    db = get_db()
    total_docs = db.execute("SELECT COUNT(*) FROM analyses WHERE owner=?", (owner,)).fetchone()[0]
    total_high = db.execute("SELECT COALESCE(SUM(high_count), 0) FROM analyses WHERE owner=?", (owner,)).fetchone()[0]
    total_medium = db.execute("SELECT COALESCE(SUM(medium_count), 0) FROM analyses WHERE owner=?", (owner,)).fetchone()[0]
    avg_score = db.execute("SELECT COALESCE(ROUND(AVG(score)), 0) FROM analyses WHERE owner=?", (owner,)).fetchone()[0]
    
    # Calculate real obligations from stored analyses
    recent_analyses = db.execute(
        "SELECT id, filename, created_at, score, level, result_json FROM analyses WHERE owner=? ORDER BY id DESC LIMIT 5",
        (owner,)).fetchall()
    
    obligations_list = []
    for row in recent_analyses:
        try:
            data = json.loads(row["result_json"])
            for n in data.get("notices", [])[:2]:
                obligations_list.append({
                    "doc": row["filename"],
                    "doc_id": row["id"],
                    "kind": "Notice Period",
                    "detail": f"{n.get('period', '')} ({n.get('about', '')})"
                })
            for p in data.get("payments", [])[:2]:
                obligations_list.append({
                    "doc": row["filename"],
                    "doc_id": row["id"],
                    "kind": "Payment Obligation",
                    "detail": f"{p.get('amount', '')} - {p.get('kind', '')} ({p.get('frequency') or p.get('condition') or 'Scheduled'})"
                })
        except Exception:
            continue

    stats = {
        "total_docs": total_docs,
        "total_high": total_high,
        "total_medium": total_medium,
        "avg_score": int(avg_score),
        "total_obligations": len(obligations_list)
    }
    return render_template("dashboard.html", stats=stats, recent=recent_analyses, obligations=obligations_list[:5])


@app.get("/analyze")
@login_required
def index():
    return render_template("index.html", recent=recent_rows(), error=None)


@app.post("/analyze")
@login_required
def analyze():
    try:
        name, ext, data = read_upload("document")
        ext = analyzer.detect_type(ext, data)
        pages = analyzer.extract_pages(ext, data)
        result = analyzer.analyze_document(pages)
    except (UserError, analyzer.ExtractionError) as exc:
        return render_template("index.html", recent=recent_rows(), error=str(exc)), 400
    except Exception:
        app.logger.exception("Analysis failed")
        return render_template("index.html", recent=recent_rows(),
                               error="Something went wrong while reading this file. Try another file."), 500

    s = result["summary"]
    cur = get_db().execute(
        "INSERT INTO analyses (owner, filename, file_type, created_at, page_count, clause_count, "
        "high_count, medium_count, low_count, score, level, result_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (current_owner(), name, ext, datetime.now().strftime("%Y-%m-%d %H:%M"), result["page_count"],
         result["clause_count"], s["high"], s["medium"], s["low"], s["score"], s["level"],
         json.dumps(result)))
    get_db().commit()
    return redirect(url_for("analysis", analysis_id=cur.lastrowid))


@app.get("/analysis/<int:analysis_id>")
@login_required
def analysis(analysis_id):
    row = get_analysis_or_404(analysis_id)
    return render_template("result.html", row=row, r=json.loads(row["result_json"]),
                           categories=analyzer.CATEGORIES)


@app.get("/analysis/<int:analysis_id>/report.pdf")
@login_required
def analysis_report(analysis_id):
    row = get_analysis_or_404(analysis_id)
    pdf = report.build_report({"filename": row["filename"], "created_at": row["created_at"]},
                              json.loads(row["result_json"]))
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=f"DocumentIQ-{analysis_id}.pdf")



@app.post("/analysis/<int:analysis_id>/exit-discard")
@login_required
def analysis_exit_discard(analysis_id):
    get_analysis_or_404(analysis_id)
    get_db().execute("DELETE FROM analyses WHERE id=? AND owner=?", (analysis_id, current_owner()))
    get_db().commit()
    flash("Exited without saving. Analysis was discarded.", "info")
    return redirect(url_for("dashboard"))

@app.post("/analysis/<int:analysis_id>/delete")
@login_required
def analysis_delete(analysis_id):
    get_analysis_or_404(analysis_id)
    get_db().execute("DELETE FROM analyses WHERE id=? AND owner=?", (analysis_id, current_owner()))
    get_db().commit()
    flash("Analysis removed from your repository.", "info")
    return redirect(url_for("history"))


@app.get("/history")
@login_required
def history():
    rows = get_db().execute(
        "SELECT id, filename, file_type, created_at, page_count, clause_count, score, level "
        "FROM analyses WHERE owner=? ORDER BY id DESC", (current_owner(),)).fetchall()
    return render_template("history.html", rows=rows)


@app.route("/compare", methods=["GET", "POST"])
@login_required
def compare():
    if request.method == "GET":
        return render_template("compare.html", error=None)
    try:
        old_name, old_ext, old_data = read_upload("old_doc")
        new_name, new_ext, new_data = read_upload("new_doc")
        old_clauses = analyzer.build_comparison_clauses(analyzer.extract_pages(old_ext, old_data))
        new_clauses = analyzer.build_comparison_clauses(analyzer.extract_pages(new_ext, new_data))
        old_cut = len(old_clauses) >= 3000
        new_cut = len(new_clauses) >= 3000
        diff = analyzer.compare_documents(old_clauses, new_clauses)
    except (UserError, analyzer.ExtractionError) as exc:
        return render_template("compare.html", error=str(exc)), 400
    except Exception:
        app.logger.exception("Comparison failed")
        return render_template("compare.html", error="Something went wrong while comparing these files."), 500
    return render_template("compare_result.html", diff=diff, old_name=old_name, new_name=new_name,
                           old_count=len(old_clauses), new_count=len(new_clauses),
                           truncated=old_cut or new_cut)


@app.get("/obligations")
@login_required
def obligations():
    owner = current_owner()
    analyses = get_db().execute("SELECT id, filename, created_at, result_json FROM analyses WHERE owner=?", (owner,)).fetchall()
    notices_all = []
    payments_all = []
    for a in analyses:
        try:
            d = json.loads(a["result_json"])
            for n in d.get("notices", []):
                notices_all.append({"filename": a["filename"], "analysis_id": a["id"], **n})
            for p in d.get("payments", []):
                payments_all.append({"filename": a["filename"], "analysis_id": a["id"], **p})
        except Exception:
            continue
    return render_template("obligations.html", notices=notices_all, payments=payments_all)


@app.get("/reports")
@login_required
def reports():
    owner = current_owner()
    docs = get_db().execute(
        "SELECT id, filename, file_type, created_at, page_count, clause_count, score, level "
        "FROM analyses WHERE owner=? ORDER BY id DESC", (owner,)).fetchall()
        
    generated = get_db().execute(
        "SELECT id, title, report_type, format, created_at, doc_count, doc_ids_json "
        "FROM generated_reports WHERE owner=? ORDER BY id DESC", (owner,)).fetchall()
        
    history = []
    for g in generated:
        history.append({
            "id": g["id"],
            "title": g["title"],
            "report_type": g["report_type"],
            "format": g["format"],
            "created_at": g["created_at"],
            "doc_count": g["doc_count"],
        })
        
    return render_template("reports.html", docs=docs, history=history)


@app.post("/reports/generate")
@login_required
def reports_generate():
    import datetime, csv, io
    owner = current_owner()
    user = get_user_record()
    
    report_type = request.form.get("report_type", "Complete Document Analysis Report")
    doc_ids = request.form.getlist("doc_ids")
    if not doc_ids:
        raw_ids = request.form.get("selected_ids", "")
        if raw_ids:
            doc_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
            
    if not doc_ids:
        flash("Please select at least one analyzed document to generate a report.", "warning")
        return redirect(url_for("reports"))
        
    report_title = request.form.get("report_title", "").strip() or f"{report_type} - {datetime.date.today()}"
    reporting_date = request.form.get("reporting_date", "") or datetime.date.today().strftime("%Y-%m-%d")
    org_name = request.form.get("org_name", "").strip() or (user.company if user else "DocumentIQ Enterprise")
    prepared_by = request.form.get("prepared_by", "").strip() or (user.full_name if user else "DocumentIQ User")
    export_format = request.form.get("export_format", "PDF").upper()
    
    options = {
        "inc_exec_summary": request.form.get("inc_exec_summary") == "on",
        "inc_doc_info": request.form.get("inc_doc_info") == "on",
        "inc_risk_score": request.form.get("inc_risk_score") == "on",
        "inc_findings": request.form.get("inc_findings") == "on",
        "inc_clauses": request.form.get("inc_clauses") == "on",
        "inc_recommendations": request.form.get("inc_recommendations") == "on",
        "inc_obligations": request.form.get("inc_obligations") == "on",
        "inc_financial": request.form.get("inc_financial") == "on",
        "inc_deadlines": request.form.get("inc_deadlines") == "on",
        "inc_comparisons": request.form.get("inc_comparisons") == "on",
        "reporting_date": reporting_date,
        "org_name": org_name,
        "prepared_by": prepared_by,
    }
    
    db = get_db()
    cur = db.execute(
        "INSERT INTO generated_reports (owner, title, report_type, format, created_at, doc_ids_json, options_json, doc_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (owner, report_title, report_type, export_format,
         datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
         json.dumps(doc_ids), json.dumps(options), len(doc_ids))
    )
    db.commit()
    report_id = cur.lastrowid
    
    if export_format == "CSV":
        placeholders = ",".join("?" * len(doc_ids))
        rows = db.execute(f"SELECT filename, result_json FROM analyses WHERE owner=? AND id IN ({placeholders})", [owner] + doc_ids).fetchall()
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Report Title", report_title])
        writer.writerow(["Generated Date", reporting_date])
        writer.writerow(["Organization", org_name])
        writer.writerow(["Prepared By", prepared_by])
        writer.writerow([])
        writer.writerow(["Document", "Category", "Level", "Title", "Explanation", "Negotiation Recommendation"])
        
        for r in rows:
            fname = r["filename"]
            if r["result_json"]:
                try:
                    res = json.loads(r["result_json"])
                    for c in res.get("flagged", []):
                        for reason in c.get("reasons", []):
                            writer.writerow([
                                fname,
                                reason.get("category", ""),
                                reason.get("level", ""),
                                reason.get("title", ""),
                                reason.get("explanation", ""),
                                reason.get("recommendation", "")
                            ])
                except Exception:
                    pass
        csv_data = output.getvalue()
        return Response(csv_data, mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={report_title.replace(' ', '_')}.csv"})

    return redirect(url_for("reports_preview", report_id=report_id))


@app.get("/reports/<int:report_id>/preview")
@login_required
def reports_preview(report_id):
    owner = current_owner()
    rep = get_db().execute(
        "SELECT * FROM generated_reports WHERE id=? AND owner=?", (report_id, owner)
    ).fetchone()
    if not rep:
        abort(404)
        
    doc_ids = json.loads(rep["doc_ids_json"])
    options = json.loads(rep["options_json"])
    
    placeholders = ",".join("?" * len(doc_ids))
    doc_rows = get_db().execute(
        f"SELECT id, filename, file_type, created_at, score, level, result_json FROM analyses WHERE owner=? AND id IN ({placeholders})",
        [owner] + doc_ids
    ).fetchall()
    
    analyzed_docs = []
    total_high = 0
    total_medium = 0
    total_low = 0
    all_obligations = []
    
    for d in doc_rows:
        res = json.loads(d["result_json"]) if d["result_json"] else {}
        s = res.get("summary", {})
        high_c = s.get("high", 0)
        med_c = s.get("medium", 0)
        low_c = s.get("low", 0)
        total_high += high_c
        total_medium += med_c
        total_low += low_c
        
        for p in res.get("payments", []):
            all_obligations.append({"doc": d["filename"], "type": "Payment", "detail": p.get("amount", ""), "when": p.get("when", "")})
        for n in res.get("notices", []):
            all_obligations.append({"doc": d["filename"], "type": "Notice", "detail": n.get("period", ""), "when": n.get("when", "")})
            
        analyzed_docs.append({
            "id": d["id"],
            "filename": d["filename"],
            "score": d["score"],
            "level": d["level"],
            "created_at": d["created_at"],
            "high": high_c,
            "medium": med_c,
            "low": low_c,
            "takeaways": s.get("takeaways", []),
            "flagged": res.get("flagged", []),
        })
        
    stats = {
        "total_docs": len(analyzed_docs),
        "total_high": total_high,
        "total_medium": total_medium,
        "total_low": total_low,
        "avg_score": round(sum(d["score"] for d in analyzed_docs) / len(analyzed_docs)) if analyzed_docs else 0,
    }
    
    return render_template("report_preview.html", rep=rep, options=options, docs=analyzed_docs, obligations=all_obligations, stats=stats)


@app.get("/reports/<int:report_id>/pdf")
@login_required
def reports_download_pdf(report_id):
    from report import build_portfolio_report
    owner = current_owner()
    rep = get_db().execute("SELECT * FROM generated_reports WHERE id=? AND owner=?", (report_id, owner)).fetchone()
    if not rep:
        abort(404)
        
    doc_ids = json.loads(rep["doc_ids_json"])
    placeholders = ",".join("?" * len(doc_ids))
    rows = get_db().execute(
        f"SELECT filename, created_at, score, level, result_json FROM analyses WHERE owner=? AND id IN ({placeholders})",
        [owner] + doc_ids
    ).fetchall()
    
    total_docs = len(rows)
    total_high = 0
    total_medium = 0
    total_low = 0
    total_obligations = 0
    scores = []
    analyses_list = []
    
    for r in rows:
        scores.append(r["score"] or 0)
        high_c = 0
        if r["result_json"]:
            try:
                res = json.loads(r["result_json"])
                s = res.get("summary", {})
                high_c = s.get("high", 0)
                total_high += high_c
                total_medium += s.get("medium", 0)
                total_low += s.get("low", 0)
                total_obligations += len(res.get("payments", [])) + len(res.get("notices", []))
            except Exception:
                pass
        analyses_list.append({
            "filename": r["filename"],
            "created_at": r["created_at"],
            "score": r["score"],
            "level": r["level"],
            "high_count": high_c,
        })
        
    avg_score = round(sum(scores) / total_docs) if total_docs > 0 else 0
    avg_level = "High" if avg_score >= 55 or total_high >= 3 else "Medium" if avg_score >= 25 else "Low"
    
    summary_stats = {
        "date": rep["created_at"],
        "total_docs": total_docs,
        "avg_score": avg_score,
        "avg_level": avg_level,
        "total_high": total_high,
        "total_medium": total_medium,
        "total_low": total_low,
        "total_obligations": total_obligations,
    }
    
    pdf_bytes = build_portfolio_report(rep["title"], analyses_list, summary_stats)
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={rep['title'].replace(' ', '_')}.pdf"})


@app.post("/reports/<int:report_id>/rename")
@login_required
def reports_rename(report_id):
    owner = current_owner()
    new_title = request.form.get("title", "").strip()
    if new_title:
        db = get_db()
        db.execute("UPDATE generated_reports SET title=? WHERE id=? AND owner=?", (new_title, report_id, owner))
        db.commit()
        flash("Report renamed successfully.", "info")
    return redirect(url_for("reports"))


@app.post("/reports/<int:report_id>/delete")
@login_required
def reports_delete(report_id):
    owner = current_owner()
    db = get_db()
    db.execute("DELETE FROM generated_reports WHERE id=? AND owner=?", (report_id, owner))
    db.commit()
    flash("Report removed from history.", "info")
    return redirect(url_for("reports"))


@app.get("/profile")
@login_required
def profile():
    return render_template("profile.html")


@app.get("/settings")
@login_required
def settings():
    return render_template("settings.html")


@app.get("/help")
@login_required
def help_support():
    return render_template("help.html")


# --------------------------------------------------------------------------
# Error Handlers
# --------------------------------------------------------------------------
MESSAGES = {
    400: "That request could not be processed.",
    404: "We could not find that page.",
    405: "That action is not allowed here.",
    413: "The upload is too large. Each file can be up to 10 MB.",
}


@app.errorhandler(HTTPException)
def http_error(exc):
    code = exc.code or 500
    message = exc.description if code == 400 and exc.description else MESSAGES.get(code, "Something went wrong.")
    return render_template("error.html", code=code, message=message), code


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
