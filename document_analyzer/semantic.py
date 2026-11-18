"""Meaning-based analysis helpers.

Instead of looking for a few keywords, this module reads every word of a sentence:
  1. words are reduced to a root form and mapped to a concept ("terminate", "cancel",
     "close", "end" all become END; "pass", "give", "sell", "disclose" become SHARE),
  2. the whole sentence is compared with example statements of each risk using
     TF-IDF cosine similarity over single words and word pairs,
  3. the party a sentence is about (you or the other side) is detected.

Pure Python: no training, no downloads, no external services.
"""
import math
import re
from collections import Counter

# --------------------------------------------------------------------------
# Tokenising and normalising
# --------------------------------------------------------------------------
_WORD = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)*|\d+")


def stem(word):
    """A small suffix stripper so that 'cancelled', 'cancelling' and 'cancel' match."""
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith(("ies", "ied")) and len(word) > 4:
        return word[:-3] + "y"
    for suffix in ("ations", "ation", "ments", "ment", "ingly", "ings", "ing", "edly",
                   "able", "ers", "ed", "es", "er", "al", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix in ("es", "s") and word.endswith("ss"):
                break
            word = word[:-len(suffix)]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "sz":
        word = word[:-1]
    return word


_FAMILIES = {
    "END": "terminate terminates terminated terminating termination cancel cancels cancelled cancelling "
           "cancellation cancellable end ends ended ending discontinue discontinued cease withdraw "
           "withdrawal quit exit leave leaving left close closed closing revoke suspend suspended "
           "suspension dissolve stop stopped stopping dismiss dismissed dismissal",
    "RENEW": "renew renews renewal renewed renewing extend extends extended extension rollover recur "
             "recurring rebill rebilled prolong continue continues continued continuing",
    "AUTO": "automatic automatically auto",
    "UNLESS": "unless until except",
    "REFUND": "refund refunds refunded refundable nonrefundable reimburse reimbursed reimbursement "
              "repay repaid repayment payback return returned returning returns returnable",
    "MONEY": "pay pays paid paying payment payments payable charge charges charged fee fees cost costs "
             "price prices pricing rate rates rent amount amounts sum instalment instalments installment "
             "installments bill bills billed billing invoice invoiced levy premium balance money funds",
    "PENALTY": "penalty penalties fine fines fined surcharge forfeit forfeited forfeiture interest",
    "LATE": "late overdue delay delayed delays arrears unpaid",
    "DEPOSIT": "deposit deposits bond prepay prepaid prepayment upfront advance hold holds held "
               "withhold withheld",
    "KEPT": "retained",
    "CHANGE": "increase increases increased raise raises raised rise rises hike escalate adjust adjusted "
              "revise revised revision vary alter amend amended update updated modify modified change "
              "changes changed edit replace rewrite rewritten",
    "TERMS": "terms conditions rules policy policies provisions regulations",
    "AGREE": "agreement contract lease tenancy subscription membership plan arrangement",
    "SHARE": "share shared sharing disclose disclosed disclosure give gives pass passed provide provides hand hands handed "
             "send transfer sell sold sale release supply reveal distribute",
    "DATA": "data information details detail record records profile",
    "THIRD": "third partner partners affiliate affiliates vendor vendors advertiser advertisers "
             "advertising marketer marketers supplier suppliers subsidiary subsidiaries companies "
             "businesses organisations organizations",
    "COLLECT": "collect collects collected collecting collection gather gathered store stored storage "
               "keep keeps kept retain retains retention track tracks tracked tracking monitor "
               "monitored log logs logged capture recorded recording",
    "PERSONAL": "location browsing history activity usage behaviour behavior cookies device",
    "PROTECT": "secure securely safe safely protect protected safeguard confidential encrypted",
    "LIABLE": "liable liability liabilities responsible responsibility accountable accountability "
              "blame fault culpable",
    "LOSS": "loss losses lose damage damages harm injury claim claims expense expenses",
    "WARRANT": "warranty warranties guarantee guarantees guaranteed",
    "COVER": "indemnify indemnity indemnification",
    "DISPUTE": "dispute disputes disagreement controversy lawsuit litigation sue proceeding proceedings",
    "ARBITR": "arbitration arbitrator arbitrate arbitral mediation",
    "COURT": "court courts judge jury tribunal",
    "WAIVE": "waive waived forgo surrender relinquish",
    "NOTICE": "notice notify notification inform warn warning request advise",
    "PRICEUP": "higher expensive pricier costlier",
    "CAN": "may can might could reserve reserves entitled right able",
    "DEDUCT": "deduct deducted deduction deductions reduce reduced less",
    "LIMIT": "limited limit limits cap capped maximum ceiling",
    "FIXED": "fixed frozen unchanged",
    "MUTUAL": "mutual mutually agreed consent",
    "EARLY": "early prematurely",
    "RISK": "risk risks",
}
_NO_INFLECT = {"interest", "fine", "rate", "rent", "bill", "record", "records", "log", "pay", "hold"}
CANON = {}
for _concept, _words in _FAMILIES.items():
    for _w in _words.split():
        CANON[_w] = _concept
        if _w in _NO_INFLECT or len(_w) < 4:
            continue
        for _v in (_w + "s", _w + "es", _w + "ed", _w + "d", _w + "ing"):
            CANON.setdefault(_v, _concept)
        if _w.endswith("e"):
            CANON.setdefault(_w[:-1] + "ing", _concept)

STOP = set("""a an the of to in on at by for with from as is are was were be been being this that these those
it its and or but if then than so such any all each which who whom whose will shall would should
must do does did has have had i me my they them their he she his her one also further under upon into out
up over via per you your yours we us our ours very more most other another same only just""".split())

NEG_WORDS = {"not", "no", "non", "never", "without", "neither", "nor", "cannot", "cant", "can't", "won't", "don't",
             "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't", "shouldn't", "wouldn't",
             "couldn't", "shan't", "none", "nothing"}

# Multi-word expressions are replaced by a single concept before comparing.
_PHRASES = {
    ("give", "up"): "WAIVE", ("go", "up"): "CHANGE", ("goes", "up"): "CHANGE",
    ("roll", "over"): "RENEW", ("rolls", "over"): "RENEW", ("rolled", "over"): "RENEW",
    ("roll", "into"): "RENEW", ("rolls", "into"): "RENEW", ("keep", "going"): "RENEW",
    ("keeps", "going"): "RENEW", ("goes", "on"): "RENEW", ("go", "on"): "RENEW",
    ("month", "to", "month"): "RENEW",
    ("by", "itself"): "AUTO", ("on", "its", "own"): "AUTO", ("do", "nothing"): "AUTO",
    ("money", "back"): "REFUND", ("pay", "back"): "REFUND", ("paid", "back"): "REFUND",
    ("give", "back"): "REFUND", ("given", "back"): "REFUND", ("hand", "back"): "REFUND",
    ("stays", "with", "us"): "KEPT", ("stay", "with", "us"): "KEPT", ("remains", "with", "us"): "KEPT",
    ("kept", "by", "us"): "KEPT", ("keep", "all"): "KEPT",
    ("at", "any", "time"): "ANYTIME", ("any", "time"): "ANYTIME", ("at", "will"): "ANYTIME",
    ("whenever",): "ANYTIME", ("at", "any", "point"): "ANYTIME", ("at", "any", "moment"): "ANYTIME",
    ("for", "any", "reason"): "ANYTIME", ("at", "its", "discretion"): "ANYTIME",
    ("at", "our", "discretion"): "ANYTIME", ("at", "their", "discretion"): "ANYTIME",
    ("sole", "discretion"): "ANYTIME", ("own", "discretion"): "ANYTIME",
    ("absolute", "discretion"): "ANYTIME", ("see", "fit"): "ANYTIME", ("as", "we", "choose"): "ANYTIME",
    ("as", "they", "choose"): "ANYTIME",
    ("from", "time", "to", "time"): "PERIODIC",
    ("without", "notice"): "NONOTICE", ("without", "prior", "notice"): "NONOTICE",
    ("without", "any", "notice"): "NONOTICE", ("without", "telling"): "NONOTICE",
    ("without", "informing"): "NONOTICE", ("without", "warning"): "NONOTICE",
    ("without", "asking"): "NONOTICE", ("without", "explanation"): "NONOTICE",
    ("without", "consent"): "NONOTICE", ("without", "your", "consent"): "NONOTICE",
    ("advance", "notice"): "NOTICE", ("let", "us", "know"): "NOTICE", ("let", "me", "know"): "NOTICE",
    ("back", "out"): "END", ("shut", "down"): "END", ("shuts", "down"): "END",
    ("hand", "over"): "SHARE", ("hold", "harmless"): "COVER", ("make", "good"): "COVER",
    ("reimburse", "us"): "COVER", ("compensate", "us"): "COVER", ("against", "all", "claims"): "COVER",
    ("against", "any", "claims"): "COVER", ("against", "all", "losses"): "COVER",
    ("third", "party"): "THIRD", ("third", "parties"): "THIRD",
    ("own", "risk"): "OWNRISK", ("at", "the", "risk", "of"): "OWNRISK", ("up", "front"): "DEPOSIT",
    ("class", "action"): "DISPUTE", ("group", "lawsuit"): "DISPUTE", ("legal", "action"): "DISPUTE",
    ("stay", "the", "same"): "FIXED", ("stays", "the", "same"): "FIXED",
    ("both", "sides"): "MUTUAL", ("both", "parties"): "MUTUAL", ("either", "party"): "MUTUAL",
    ("written", "agreement"): "MUTUAL", ("by", "agreement"): "MUTUAL", ("mutual", "agreement"): "MUTUAL",
    ("not", "exceed"): "LIMIT", ("shall", "not", "exceed"): "LIMIT", ("limited", "to"): "LIMIT",
}
_MAX_PHRASE = max(len(k) for k in _PHRASES)


def analyse(text):
    """Return [(token, start, end)] with stop words removed and concepts substituted."""
    raw = []
    for m in _WORD.finditer(text[:2500]):
        w = m.group().lower().replace("\u2019", "'")
        if w.endswith("'s"):
            w = w[:-2]
        raw.append((w, m.start(), m.end()))
    out, i = [], 0
    while i < len(raw):
        for size in range(min(_MAX_PHRASE, len(raw) - i), 1, -1):
            key = tuple(t[0] for t in raw[i:i + size])
            if key in _PHRASES:
                out.append((_PHRASES[key], raw[i][1], raw[i + size - 1][2]))
                i += size
                break
        else:
            w, s, e = raw[i]
            i += 1
            if (w,) in _PHRASES:
                out.append((_PHRASES[(w,)], s, e))
            elif w in NEG_WORDS:
                out.append(("NEG", s, e))
            elif w in STOP:
                continue
            elif w.isdigit():
                continue
            else:
                out.append((CANON.get(w) or stem(w), s, e))
    return out


def concept_tokens(text):
    return {t for t, _, _ in analyse(text)}


def _features(tokens):
    feats = Counter(tokens)
    for a, b in zip(tokens, tokens[1:]):
        feats[a + "_" + b] += 1
    return feats


# --------------------------------------------------------------------------
# Example statements for each risk (paraphrases, not a keyword list)
# --------------------------------------------------------------------------
INTENTS = {
    "renew_auto": [
        "The contract continues for another term unless you tell us to stop.",
        "Your plan rolls over to the next period automatically.",
        "If you do not cancel before the end date the subscription extends for a further year.",
        "The agreement is extended each year without any action from you.",
        "You will be billed again every cycle until you end the membership.",
        "This agreement automatically renews for successive multi-year periods.",
    ],
    "renew_price": [
        "The price may go up when your plan renews.",
        "Renewal fees can be higher than the original rate.",
        "Rent will be adjusted at the time of renewal.",
    ],
    "cancel_fee": [
        "You must pay a charge if you leave before the end of the contract.",
        "Ending the agreement early will cost you the remaining months.",
        "A penalty is payable if you terminate early.",
        "If you stop the service before the minimum term you owe the rest of the fees.",
        "There is an exit fee for cancelling during the first year.",
    ],
    "cancel_blocked": [
        "This contract cannot be ended before the term is over.",
        "You have no right to withdraw from this agreement once it is signed.",
        "The subscription is not cancellable.",
    ],
    "cancel_provider": [
        "We can close your account whenever we choose.",
        "The company may end this agreement at its own discretion without telling you.",
        "We reserve the right to suspend your access at any time without notice.",
        "The landlord may terminate the tenancy whenever they see fit.",
    ],
    "cancel_notice": [
        "You need to tell us in writing a month before you leave.",
        "Notice must be given at least thirty days before ending the agreement.",
        "To cancel, you must inform us ahead of the renewal date.",
        "Written warning is required before the contract can be terminated.",
    ],
    "refund_none": [
        "Money paid will not be returned under any circumstances.",
        "No money back once payment is made.",
        "We do not give back fees for unused time.",
        "Amounts paid stay with us if you leave.",
        "Payments are final and cannot be reimbursed.",
        "You lose the deposit if you cancel.",
    ],
    "refund_limited": [
        "Any money returned may be reduced by an administration charge.",
        "Only a partial amount of your payment may be paid back at our discretion.",
        "We may deduct costs from the amount refunded to you.",
    ],
    "pay_late": [
        "If you pay after the due date you will be charged extra.",
        "Interest is added to unpaid balances.",
        "Delayed instalments attract a fine.",
        "A penalty applies to overdue payments.",
    ],
    "pay_deposit": [
        "You must hand over a sum in advance before moving in which we hold.",
        "A bond is kept by the owner until you leave.",
        "An upfront payment is required before the service starts.",
    ],
    "pay_change": [
        "We can raise the price whenever we wish.",
        "The cost of the service may go up during the term.",
        "Fees are subject to revision by us at any time.",
        "The rent may be increased by the owner.",
    ],
    "privacy_share": [
        "We may pass your details to other companies for marketing.",
        "Your information can be given to our business partners.",
        "We can sell the data we hold about you.",
        "Personal data may be disclosed to advertisers and affiliates.",
        "We share your records with outside organisations.",
    ],
    "privacy_collect": [
        "We keep a record of where you are and what you do on the site.",
        "Information about your device and usage is gathered.",
        "We track your browsing activity and location.",
        "Personal details are stored by us for an indefinite period.",
    ],
    "liab_limit": [
        "We will not be responsible for any loss you suffer.",
        "The company accepts no responsibility for damage.",
        "You use the service entirely at your own risk.",
        "No guarantee is given about the quality of the service.",
        "We exclude all warranties to the fullest extent allowed.",
    ],
    "liab_cap": [
        "Our total responsibility is limited to the fees you paid.",
        "Liability shall not exceed the fees paid under this agreement.",
    ],
    "liab_indemnity": [
        "You agree to cover our costs if anyone makes a claim because of your actions.",
        "You will compensate us for any legal expenses we incur.",
        "You must protect us against all losses arising from your use.",
    ],
    "dispute": [
        "Any disagreement must be settled by a private arbitrator not a judge.",
        "You give up the right to take us to court.",
        "You cannot join with other customers to bring a group lawsuit.",
        "Disputes will only be heard in the courts of our home city.",
    ],
    "change_unilateral": [
        "We can alter these terms whenever we like without telling you.",
        "The company may edit the rules at any time and you are bound by the new version.",
        "Changes to the policy take effect immediately without prior notice.",
    ],
    "change_right": [
        "We may update this policy from time to time.",
        "The provider keeps the right to revise these conditions.",
        "These terms can be amended by us.",
    ],
}


# --------------------------------------------------------------------------
# Concept rules: combinations of ideas, whatever words express them
# (weight, window in words, concepts that must all appear close together)
# --------------------------------------------------------------------------
CONCEPTS = {
    "renew_auto": [(0.85, 8, "RENEW", "AUTO"), (0.75, 10, "RENEW", "UNLESS")],
    "renew_price": [(0.70, 8, "RENEW", "MONEY", "CHANGE"), (0.65, 8, "RENEW", "PRICEUP")],
    "cancel_fee": [(0.85, 8, "END", "PENALTY"), (0.80, 8, "END", "LIQUIDATED")],
    "cancel_notice": [(0.70, 6, "NOTICE", "END"), (0.65, 8, "END", "NOTICE")],
    "pay_late": [(0.80, 8, "LATE", "PENALTY"), (0.75, 8, "LATE", "MONEY"), (0.70, 8, "OVERDUE", "MONEY")],
    "cancel_blocked": [(0.70, 2, "NEG", "END", "AGREE")],
    "cancel_provider": [(0.85, 7, "END", "ANYTIME"), (0.85, 8, "END", "NONOTICE")],
    "cancel_notice": [(0.70, 6, "NOTICE", "END")],
    "refund_none": [(0.78, 4, "REFUND", "NEG"), (0.78, 6, "KEPT", "MONEY")],
    "refund_limited": [(0.75, 8, "REFUND", "DEDUCT"), (0.75, 8, "REFUND", "ANYTIME")],
    "pay_late": [(0.80, 8, "LATE", "PENALTY"), (0.70, 8, "LATE", "MONEY"), (0.60, 8, "MONEY", "PENALTY")],
    "pay_deposit": [(0.65, 8, "DEPOSIT", "MONEY")],
    "pay_change": [(0.85, 8, "CHANGE", "MONEY", "ANYTIME"), (0.55, 6, "CHANGE", "MONEY"), (0.80, 8, "PRICEUP", "ANYTIME"),
                   (0.60, 6, "PRICEUP", "CAN")],
    "privacy_share": [(0.90, 10, "SHARE", "DATA", "THIRD"), (0.75, 8, "SHARE", "THIRD")],
    "privacy_collect": [(0.70, 8, "COLLECT", "PERSONAL"), (0.60, 8, "COLLECT", "DATA")],
    "liab_limit": [(0.85, 3, "NEG", "LIABLE"), (0.85, 1, "OWNRISK"),
                   (0.80, 3, "NEG", "WARRANT")],
    "liab_cap": [(0.80, 8, "LIABLE", "LIMIT")],
    "liab_indemnity": [(0.90, 8, "COVER", "LOSS"), (0.80, 1, "COVER")],
    "dispute": [(0.85, 1, "ARBITR"), (0.90, 8, "WAIVE", "COURT"), (0.90, 8, "WAIVE", "DISPUTE"),
                (0.60, 4, "NEG", "DISPUTE"), (0.55, 8, "DISPUTE", "COURT")],
    "change_unilateral": [(0.90, 10, "CHANGE", "TERMS", "ANYTIME"), (0.90, 10, "CHANGE", "TERMS", "NONOTICE"),
                          (0.65, 8, "CHANGE", "NONOTICE")],
    "change_right": [(0.60, 8, "CHANGE", "TERMS", "CAN")],
}
# If any of these ideas is also present, the clause is probably protective: weight is cut.
GUARDS = {
    "pay_change": {"LIMIT", "FIXED", "MUTUAL"}, "renew_price": {"LIMIT", "FIXED"},
    "change_right": {"MUTUAL"}, "change_unilateral": {"MUTUAL"}, "cancel_provider": {"MUTUAL"},
    "privacy_collect": {"PROTECT"}, "privacy_share": {"PROTECT"}, "pay_deposit": {"REFUND"},
}


# The idea every concept rule of an intent shares (for example END for "other party can end it").
# A similarity match without it is ignored, because generic words alone are not evidence.
CORE = {intent: set.intersection(*[set(alt[2:]) for alt in alts]) for intent, alts in CONCEPTS.items()}


def guard_active(intent, names):
    """Protective wording is present, so the clause is probably benign."""
    if intent in ("privacy_share", "privacy_collect") and "PROTECT" in names:
        return True
    return bool(GUARDS.get(intent, set()) & names) and "NEG" not in names


def _closest(positions, need, window):
    from itertools import product
    lists = [positions[c][:6] for c in need]
    best = None
    for combo in product(*lists):
        if len(set(combo)) < len(combo):
            continue
        spread = max(combo) - min(combo)
        if spread <= window and (best is None or spread < best[0]):
            best = (spread, combo)
    return None if best is None else list(best[1])


def concept_matches(tokens):
    """tokens: [(name, start, end)]. Returns {intent: (weight, [(start, end), ...])}."""
    positions = {}
    for i, (name, _, _) in enumerate(tokens):
        positions.setdefault(name, []).append(i)
    found = {}
    for intent, alts in CONCEPTS.items():
        best = None
        for weight, window, *need in alts:
            if any(c not in positions for c in need):
                continue
            combo = _closest(positions, need, window)
            if combo is None:
                continue
            w = weight * (0.45 if guard_active(intent, set(positions)) else 1.0)
            if best is None or w > best[0]:
                best = (w, _runs([(tokens[i][1], tokens[i][2]) for i in sorted(combo)]))
        if best:
            found[intent] = best
    return found

_NEUTRAL = {"NEG", "NUM", "CAN"}
_INDEX = []          # (intent_id, text, vector, norm, unigram tokens)
_DF = Counter()


def _build_index():
    docs = []
    for intent, examples in INTENTS.items():
        for text in examples:
            toks = [t for t, _, _ in analyse(text)]
            docs.append((intent, text, toks, _features(toks)))
    for _, _, _, feats in docs:
        for f in feats:
            _DF[f] += 1
    n = len(docs)
    for intent, text, toks, feats in docs:
        vec = {f: (1 + math.log(c)) * (math.log((1 + n) / (1 + _DF[f])) + 1) for f, c in feats.items()}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        _INDEX.append((intent, text, vec, norm, set(toks)))
    return n


_N_DOCS = _build_index()
_UNKNOWN_IDF = math.log(1 + _N_DOCS) + 1


def match_intents(text):
    """Compare a sentence with every example. Returns {intent: info} for the best example of each."""
    tokens = analyse(text)
    if not tokens:
        return {}
    names = [t for t, _, _ in tokens]
    feats = _features(names)
    qvec = {f: (1 + math.log(c)) * ((math.log((1 + _N_DOCS) / (1 + _DF[f])) + 1) if f in _DF else _UNKNOWN_IDF)
            for f, c in feats.items()}
    qnorm = math.sqrt(sum(v * v for v in qvec.values()))
    if qnorm == 0:
        return {}
    best = {}
    for intent, example, vec, norm, uni in _INDEX:
        small, big = (qvec, vec) if len(qvec) < len(vec) else (vec, qvec)
        dot = sum(w * big[f] for f, w in small.items() if f in big)
        score = dot / (qnorm * norm) if norm else 0.0
        if intent not in best or score > best[intent]["score"]:
            shared = (set(names) & uni) - _NEUTRAL
            best[intent] = {"score": score, "example": example, "shared": shared}
    concepts = concept_matches(tokens)
    for intent in set(best) | set(concepts):
        info = best.setdefault(intent, {"score": 0.0, "example": "", "shared": set()})
        info["spans"] = _runs([(s, e) for t, s, e in tokens if t in info["shared"]])
        info["concept"], info["concept_spans"] = concepts.get(intent, (0.0, []))
        info["guard"] = guard_active(intent, set(names))
        info["core_ok"] = CORE.get(intent, set()) <= set(names)
        info["names"] = set(names)
    return best


def _runs(spans, gap=14, limit=3):
    """Merge nearby highlighted words into a few readable runs."""
    merged = []
    for s, e in spans:
        if merged and s - merged[-1][1] <= gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    merged.sort(key=lambda r: r[0] - r[1])      # longest first
    return sorted(merged[:limit])


# --------------------------------------------------------------------------
# Who is the sentence about?
# --------------------------------------------------------------------------
_YOU = r"you|your|yours|tenants?|lessees?|customers?|users?|subscribers?|members?|employees?|buyers?|" \
       r"purchasers?|consumers?|borrowers?|students?|licensees?|guests?|residents?|occupants?|renters?|" \
       r"patients?|policyholders?"
_OTHER = r"we|us|our|ours|landlords?|lessors?|owners?|company|provider|employer|sellers?|suppliers?|" \
         r"vendors?|licensors?|lenders?|bank|insurers?|operators?|hosts?|management|firm|agency|platform"
_BOTH = r"either\s+party|both\s+parties|each\s+party|the\s+parties|any\s+party"
_PARTY_RE = re.compile(rf"\b(?:(?P<both>{_BOTH})|(?P<you>{_YOU})|(?P<other>{_OTHER}))\b", re.I)
_POSSESSIVE = {"your", "yours", "our", "ours"}


def party_near(text, pos):
    """Return 'you', 'other', 'both' or None for the party that comes closest before a position.
    Possessives ("your contract", "our site") are ignored because they only describe things."""
    hits = []
    for m in _PARTY_RE.finditer(text):
        if m.group(0).lower() in _POSSESSIVE:
            continue
        hits.append((m.start(), "both" if m.group("both") else "you" if m.group("you") else "other"))
    before = [h for h in hits if h[0] < pos and pos - h[0] <= 90]
    return max(before)[1] if before else None