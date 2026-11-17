"""One-time updater: adds meaning-based analysis to the Document Risk & Obligation Analyzer.

1. Put semantic.py next to app.py.
2. Put this file next to app.py and run:   python update_to_meaning.py
It edits analyzer.py, report.py, templates/result.html, static/css/style.css and static/js/app.js.
It stops with a clear message, without changing anything, if a file does not look like the expected version.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def load(rel):
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        sys.exit(f"Missing file: {rel}. Run this script from the project folder (next to app.py).")
    with open(path, encoding="utf-8", newline="") as fh:
        return path, fh.read().replace("\r\n", "\n")


def edit(text, rel, old, new):
    if old not in text:
        sys.exit(f"Could not update {rel}: the expected text was not found.\nNothing has been changed. "
                 f"Send this message to get a full replacement file.")
    return text.replace(old, new, 1)


if not os.path.exists(os.path.join(ROOT, "semantic.py")):
    sys.exit("semantic.py is missing. Create it next to app.py first.")

done = {}

# ---------------------------------------------------------------- analyzer.py
p, t = load("analyzer.py")
if "import semantic" in t:
    sys.exit("analyzer.py already contains the meaning layer. Nothing to do.")
t = edit(t, "analyzer.py", "import difflib\nimport io\nimport re\nimport zipfile\n",
         "import difflib\nimport io\nimport re\nimport zipfile\n\nimport semantic\n")
t = edit(t, "analyzer.py",
         'def analyze_clause(text):\n    """Return a list of reasons (dicts) for one clause, highest level first."""',
         'def _analyze_rules_only(text):\n    """Keyword rules only (the original method). Kept for comparison and testing."""')
BLOCK = r'''# ---- Meaning and context layer -------------------------------------------
SEM_MIN = 0.38        # minimum whole-sentence similarity before a meaning match is considered
MIN_CONF = 0.40       # findings below this confidence are dropped
# Who must hold the power or burden for the clause to be a risk to the reader.
# "other": a risk when the other side holds it; favourable when the sentence is about "you".
# "you":   a risk when you carry it; favourable when the sentence is about the other side.
RISK_ACTOR = {"cancel_provider": "other", "pay_change": "other", "change_unilateral": "other",
              "change_right": "other", "privacy_share": "other", "privacy_collect": "other",
              "liab_limit": "other", "liab_indemnity": "you", "liab_mention": "you"}
ACTOR_EXEMPT = {"liab_limit": {"OWNRISK"}}
SECTION_HINTS = {
    "Renewal": {"RENEW"}, "Cancellation": {"END", "NOTICE"}, "Refund": {"REFUND"},
    "Payment": {"MONEY", "LATE", "DEPOSIT", "PENALTY"}, "Privacy": {"DATA", "COLLECT", "SHARE", "privacy"},
    "Liability": {"LIABLE", "LOSS", "COVER", "WARRANT"}, "Disputes": {"DISPUTE", "ARBITR", "COURT"},
    "Changes": {"CHANGE", "TERMS"},
}
ACTOR_PLAIN = ("This wording gives the right, or the burden, to the other party rather than to you, "
               "so it usually works in your favour. Read the full sentence to be sure.")
DEFINITION_PLAIN = ("This looks like a definition of a term rather than a duty or a risk, "
                    "so it is shown with low attention.")
_DEFINITION_RE = re.compile(
    r"^[^.]{0,60}\b(?:means|shall mean|refers? to|is defined as|are defined as|has the meaning)\b", re.I)


def confidence_label(conf):
    return "Strong match" if conf >= 75 else "Likely" if conf >= 55 else "Possible, please review"


def analyze_clause(text, section="", mode="hybrid"):
    """Return a list of reasons (dicts) for one clause, highest level first.

    hybrid: keyword rules + whole-sentence meaning + context (negation, who the sentence is
            about, definitions, section heading). Each reason carries a confidence score.
    rules:  keyword rules only.
    """
    if mode == "rules":
        return _analyze_rules_only(text)
    sem = semantic.match_intents(text)
    hint_tokens = semantic.concept_tokens(section) if section else set()
    definition = bool(_DEFINITION_RE.search(text))
    short = len(text.split()) < 4
    best = {}
    for rule in RULES:
        m = None
        for rx in rule["patterns"]:
            m = rx.search(text)
            if m:
                break
        info = sem.get(rule["id"])
        hinted = bool(hint_tokens & SECTION_HINTS[rule["category"]])
        cos_hit = (bool(info) and info["score"] >= SEM_MIN - (0.05 if hinted else 0.0)
                   and len(info["shared"]) >= 2 and info["core_ok"])
        cos_conf = max(0.0, min(1.0, (info["score"] - 0.25) / 0.40)) * 0.85 if cos_hit else 0.0
        concept_conf = info["concept"] if info else 0.0
        sem_conf = max(cos_conf, concept_conf)
        sem_hit = sem_conf > 0
        if not m and not sem_hit:
            continue

        phrase_conf = (0.72 if rule["level"] != "Low" else 0.5) if m else 0.0
        conf = 1 - (1 - phrase_conf) * (1 - sem_conf)

        if m:
            start, end = m.start(), m.end()
            if end - start > 70:
                end = start + 70
                while end < len(text) and not text[end].isspace():
                    end += 1
            spans = [[start, end]]
            matched = re.sub(r"\s+", " ", m.group(0)).strip()
            if len(matched) > 90:
                matched = matched[:87].rstrip() + "..."
            detection = "both" if sem_hit else "phrase"
        else:
            chosen = info["concept_spans"] if concept_conf >= cos_conf and info["concept_spans"] else info["spans"]
            spans = [list(sp) for sp in chosen] or [[0, min(len(text), 40)]]
            start = spans[0][0]
            matched = ", ".join(text[a:b] for a, b in spans)
            matched = matched if len(matched) <= 90 else matched[:87].rstrip() + "..."
            detection = "meaning"

        negated = bool(rule["neg"] and _is_negated(text, start))
        favourable = False
        actor = RISK_ACTOR.get(rule["id"])
        if actor and info and ACTOR_EXEMPT.get(rule["id"], set()) & info["names"]:
            actor = None        # e.g. "you use it at your own risk": the sentence is about you, and it is a risk
        if actor and not negated:
            party = semantic.party_near(text, start)
            favourable = (actor == "other" and party == "you") or (actor == "you" and party == "other")
        notes = []
        if hinted:
            conf = min(0.98, conf + 0.08)
            notes.append(f"The section heading ({section}) points the same way.")
        if definition:
            conf -= 0.30
        if short:
            conf -= 0.15
        if conf < MIN_CONF:
            continue

        level = rule["level"]
        protective = bool(info and info["guard"])
        if protective and not (negated or favourable or definition):
            level = {"High": "Medium", "Medium": "Low", "Low": "Low"}[level]
            notes.append("Protective wording in the same sentence lowers the attention level.")
        if negated or favourable or definition:
            level = "Low"
        elif level == "High" and conf < 0.60:
            level = "Medium"
        explanation = (NEGATED_PLAIN if negated else ACTOR_PLAIN if favourable
                       else DEFINITION_PLAIN if definition else rule["plain"])
        cand = {
            "category": rule["category"], "level": level, "title": rule["title"], "matched": matched,
            "explanation": explanation, "negated": negated, "favourable": favourable,
            "span": spans[0], "spans": spans, "confidence": round(conf * 100),
            "confidence_label": confidence_label(round(conf * 100)), "detection": detection,
            "similar_to": info["example"] if info and detection != "phrase" else "", "notes": notes,
        }
        key = (LEVELS[level], cand["confidence"])
        cur = best.get(rule["category"])
        if cur is None or key > (LEVELS[cur["level"]], cur["confidence"]):
            best[rule["category"]] = cand
    reasons = list(best.values())
    reasons.sort(key=lambda r: (-LEVELS[r["level"]], -r["confidence"], CATEGORIES.index(r["category"])))
    return reasons


'''
t = edit(t, "analyzer.py", "def _top_level(reasons):", BLOCK + "def _top_level(reasons):")
t = edit(t, "analyzer.py", r'''    for r in reasons:
        s, e = r["span"]
        for i in range(s, min(e, len(text))):
            marks[i] = max(marks[i], LEVELS[r["level"]])''', r'''    for r in reasons:
        for s, e in r.get("spans") or [r["span"]]:
            for i in range(s, min(e, len(text))):
                marks[i] = max(marks[i], LEVELS[r["level"]])''')
t = edit(t, "analyzer.py", 'reasons = analyze_clause(c["text"])\n        if not reasons:',
         'reasons = analyze_clause(c["text"], c.get("section", ""))\n        if not reasons:')
t = edit(t, "analyzer.py", 'reasons = analyze_clause(clause["text"])\n    return {"clause": clause',
         'reasons = analyze_clause(clause["text"], clause.get("section", ""))\n    return {"clause": clause')
done[p] = t

# ---------------------------------------------------------------- report.py
p, t = load("report.py")
t = edit(t, "report.py", r'''            block.append(Paragraph(
                f'<font color="{rc}"><b>{_t(r["category"])}, {_t(r["level"])}:</b></font> '
                f'<b>{_t(r["title"])}.</b> Matched "{_t(r["matched"])}". {_t(r["explanation"])}', S["reason"]))''',
r'''            how = {"meaning": "Found by meaning", "both": "Found by wording and meaning"}.get(r.get("detection"), "Matched")
            sure = f' ({_t(r["confidence_label"])}, {r["confidence"]}%)' if "confidence" in r else ""
            block.append(Paragraph(
                f'<font color="{rc}"><b>{_t(r["category"])}, {_t(r["level"])}:</b></font> '
                f'<b>{_t(r["title"])}.</b> {how}: "{_t(r["matched"])}"{sure}. {_t(r["explanation"])}', S["reason"]))''')
done[p] = t

# ---------------------------------------------------------------- result.html
p, t = load("templates/result.html")
t = edit(t, "templates/result.html", '''    <label class="field grow"><span class="sr">Search clauses</span>''',
'''    <label class="check"><input type="checkbox" data-sure> <span>Hide low-confidence matches</span></label>
    <label class="field grow"><span class="sr">Search clauses</span>''')
t = edit(t, "templates/result.html",
'''             data-order="{{ loop.index0 }}" data-rank="{{ {'High': 3, 'Medium': 2, 'Low': 1}[c.level] }}">''',
'''             data-order="{{ loop.index0 }}" data-rank="{{ {'High': 3, 'Medium': 2, 'Low': 1}[c.level] }}"
             data-conf="{{ (c.reasons|map(attribute='confidence')|list|max) if c.reasons and c.reasons[0].confidence is defined else 100 }}">''')
t = edit(t, "templates/result.html",
'''            <div class="r-head"><span class="chip {{ x.level|slug }}">{{ x.category }}: {{ x.level }}</span><b>{{ x.title }}</b></div>
            <p class="why">Detected because the text contains "{{ x.matched }}".</p>
            <p class="plain">{{ x.explanation }}</p>''',
'''            <div class="r-head">
              <span class="chip {{ x.level|slug }}">{{ x.category }}: {{ x.level }}</span><b>{{ x.title }}</b>
              {% if x.confidence is defined %}
              <span class="conf {{ 'strong' if x.confidence >= 75 else 'likely' if x.confidence >= 55 else 'possible' }}"
                    title="How sure the analyzer is about this finding">{{ x.confidence_label }} {{ x.confidence }}%</span>
              {% endif %}
            </div>
            {% if x.detection == 'meaning' %}
            <p class="why">Found by meaning, not by an exact phrase. The words "{{ x.matched }}" express an idea close to: "{{ x.similar_to }}"</p>
            {% elif x.detection == 'both' %}
            <p class="why">Found from the wording "{{ x.matched }}" and confirmed by the meaning of the whole sentence.</p>
            {% else %}
            <p class="why">Detected because the text contains "{{ x.matched }}".</p>
            {% endif %}
            <p class="plain">{{ x.explanation }}</p>
            {% if x.notes %}<p class="ctx">{{ x.notes|join(' ') }}</p>{% endif %}''')
done[p] = t

# ---------------------------------------------------------------- style.css
p, t = load("static/css/style.css")
t = edit(t, "static/css/style.css", "/* Animations */", r'''/* Confidence and context */
.conf { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .8rem; font-weight: 650; border: 1px solid transparent; }
.conf.strong { background: #e2f5ea; color: #14683d; border-color: #bfe5cf; }
.conf.likely { background: #eef0fa; color: var(--brand-dark); border-color: #d3d8f1; }
.conf.possible { background: #fff6dd; color: #8a5a00; border-color: #f1dfa6; }
.ctx { margin-top: 4px; color: var(--muted); font-size: .86rem; font-style: italic; }
.check { display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--card); font-size: .92rem; cursor: pointer; min-height: 40px; }
.check input { width: 16px; height: 16px; accent-color: var(--brand); cursor: pointer; }

/* Animations */''')
done[p] = t

# ---------------------------------------------------------------- app.js
p, t = load("static/js/app.js")
t = edit(t, "static/js/app.js", '''    var search = toolbar.querySelector("[data-search]");
    var level = "all";''', '''    var search = toolbar.querySelector("[data-search]");
    var sure = toolbar.querySelector("[data-sure]");
    var level = "all";''')
t = edit(t, "static/js/app.js", '''                 (!q || card.textContent.toLowerCase().indexOf(q) !== -1);''',
'''                 (!sure.checked || +card.dataset.conf >= 55) &&
                 (!q || card.textContent.toLowerCase().indexOf(q) !== -1);''')
t = edit(t, "static/js/app.js", '''    search.addEventListener("input", apply);''', '''    search.addEventListener("input", apply);
    sure.addEventListener("change", apply);''')
done[p] = t

# every edit succeeded: write all files
for path, text in done.items():
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
print("Done. Updated:", ", ".join(os.path.relpath(p, ROOT) for p in done))
print("Now run:  python app.py")