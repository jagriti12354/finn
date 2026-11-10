"""Rule-first announcement classifier.

Design choice (worth stating in the writeup): exchange filings are highly templated,
so rules classify most of them for free. BSE's own subcategory is checked first because it
is the strongest signal, then ordered headline rules. Only what falls through is worth
sending to a cheap LLM, and a headline with no content needs the PDF's first page too.
`stats()` reports coverage, which is not the same thing as accuracy.

`group_events()` then folds the several filings one event produces into a single card.
"""
from __future__ import annotations
import re
from datetime import datetime

# Checked against BSE's subcategory only. First match wins.
SUBCATEGORY_RULES: list[tuple[str, str]] = [
    ("unclassified",      r"newspaper publication|repayment of commercial paper|allotment of esop|monitoring agency"),
    ("investor_meetings", r"analyst / investor meet"),
    ("earnings_call",     r"earnings call transcript"),
    ("agm_egm",           r"^(agm|egm)$"),
    ("postal_ballot",     r"^postal ballot$"),
    ("press_release",     r"award of order|receipt of order"),
    ("fund_raise",        r"issue of securities"),
    ("litigation",        r"corporate insolvency"),
]

# Checked against subcategory + headline + category. Order matters: first match wins.
RULES: list[tuple[str, list[str]]] = [
    # Routine filings go first: many of them mention "results" or a rating without being one.
    ("unclassified",      [r"newspaper", r"published results", r"(redemption|repayment) of commercial paper", r"\besg\b", r"brsr",
                           r"\besops?\b", r"\besos\b", r"stock options?", r"grant of .{0,20}options", r"incentive plan",
                           r"share transfer agent", r"change .{0,40}corporate office", r"\biepf\b", r"investor education and protection",
                           r"monitoring agency", r"certificate (under|pursuant)", r"strike.?off", r"compliance certificate",
                           r"regulation 74", r"loss of share certificate", r"duplicate share", r"trading window", r"reg(ulation)? ?40\(", r"secretarial"]),
    ("postal_ballot",     [r"postal ballot", r"voting results", r"e-?voting"]),
    ("pledging",          [r"\bpledg", r"encumbran"]),
    ("insider_trading",   [r"\bsast\b", r"regulation 7\(2\)", r"reg(ulation)?\.? ?29\(\d\).*(substantial|takeover)", r"insider trading", r"acquisition of shares by promoter", r"disclosure under regulation 31"]),
    ("credit_rating",     [r"credit rating", r"\bcrisil\b", r"\bicra\b", r"\bcare ratings\b", r"india ratings", r"\brating\b.*(assign|reaffirm|upgrad|downgrad)", r"re-?affirmation of rating"]),
    ("management_change", [r"\bresign", r"\bappoint", r"cessation", r"\bretire", r"change in (key )?(managerial|management|director)", r"change in (the )?(office of )?(managing director|md\b|ceo|chief)", r"\bkmp\b", r"re-?designat"]),
    ("M&A",               [r"acqui(re|sition)", r"\bmerger\b", r"amalgamat", r"demerg", r"joint venture", r"scheme of arrangement", r"stake sale", r"divest", r"business transfer", r"slump sale", r"wholly.owned subsidiary.*(incorporat|invest)"]),
    ("fund_raise",        [r"\bqip\b", r"rights issue", r"preferential", r"buy-?back", r"commercial paper", r"non-convertible debenture", r"\bncds?\b", r"fund raising", r"raising of funds", r"allotment of (bonds|debentures)",
                           r"initial public offer", r"\bipo\b", r"red herring", r"issue price", r"warrants", r"trading in equity shares .* will commence"]),
    # Before litigation, so an order win that names an "authority" is not read as a legal order.
    ("press_release",     [r"\b(bags?|bagged|wins?|secures?)\b.{0,40}\borders?\b", r"\((significant|large|major|mega|ultra-?mega)\*\)"]),
    ("litigation",        [r"litigation", r"\border\b.*(sebi|tribunal|court|nclt|nclat|authority|gst|income tax)", r"show cause", r"\bpenalt", r"\bfine\b", r"insolvency", r"\bncl[a]?t\b", r"demand notice", r"\bdispute"]),
    ("bonus_split",       [r"\bbonus\b", r"sub-?division", r"stock split", r"split of (equity )?shares"]),
    ("dividend",          [r"dividend"]),
    ("capex",             [r"\bcapex\b", r"capacity expansion", r"new (plant|facility|unit)", r"commission(ing|ed) of", r"greenfield", r"brownfield"]),
    ("earnings_call",     [r"earnings call", r"conference call", r"concall", r"transcript", r"audio recording", r"call recording"]),
    # A parent company's results filed for information, such as Maruti reporting Suzuki's. Not this company's numbers.
    ("press_release",     [r"results issued by"]),
    ("results",           [r"financial results", r"\bresults\b", r"interim condensed", r"outcome of board meeting", r"outcome of the board meeting", r"investor presentation", r"shareholders?'? letter", r"press release.*results"]),
    ("investor_meetings", [r"analyst", r"investor meet", r"institutional investor", r"\bnon-deal roadshow\b", r"investor conference", r"schedule of (analyst|investor)"]),
    ("agm_egm",           [r"\bagm\b", r"\begm\b", r"annual general meeting", r"extra-?ordinary general meeting", r"book closure", r"scrutini[sz]er"]),
    ("unclassified",      [r"meeting of the board of directors .*scheduled"]),
    ("press_release",     [r"press release", r"media statement", r"\border(s)? (win|received|bagged)", r"\blaunch", r"partnership", r"clarification", r"\bmou\b", r"business update", r"production volume"]),
]
_SUBCATEGORY = [(cat, re.compile(p, re.I)) for cat, p in SUBCATEGORY_RULES]
_COMPILED = [(cat, [re.compile(p, re.I) for p in pats]) for cat, pats in RULES]

# Priority tiers drive the digest ordering: 1 = surface first, 3 = collapse.
PRIORITY = {
    "results": 1, "M&A": 1, "management_change": 1, "fund_raise": 1, "litigation": 1,
    "credit_rating": 1, "pledging": 1,
    "capex": 2, "insider_trading": 2, "dividend": 2, "bonus_split": 2, "press_release": 2,
    "earnings_call": 3, "investor_meetings": 3, "agm_egm": 3, "postal_ballot": 3, "unclassified": 3,
}

# A filing announcing that the board *will* meet, as opposed to what it decided.
_INTIMATION = re.compile(r"(intimation|prior intimation|notice) of (the )?board meeting|board meeting (to be held|intimation)|meeting of the board of directors .*scheduled", re.I)
_NOTICE_STAYS_P2 = {"fund_raise", "M&A", "bonus_split"}
# Management changes below KMP level (e.g. senior management, company secretary) are lower signal.
_TOP_ROLE = re.compile(r"\b(ceo|cfo|md|managing director|chairman|chairperson|whole[- ]time director|chief (executive|financial|operating))\b", re.I)
# Setting up a new subsidiary is worth a look, but it is not a deal.
_SUBSIDIARY_SETUP = re.compile(r"incorporat", re.I)
# Nothing to classify in these headlines; the content is only in the PDF.
_VAGUE = re.compile(r"^(please find( the)? attached( \w+)?\.?|announcement as attached\.?|(disclosure|intimation|announcement) under regulation 30\b[^-]*)$", re.I)


def _norm(s: str | None) -> str:
    return " ".join((s or "").split())


def _match(sub: str, text: str) -> tuple[str, str] | None:
    for cat, p in _SUBCATEGORY:
        if p.search(sub):
            return cat, f"bse: {p.pattern}"
    for cat, pats in _COMPILED:
        for p in pats:
            if p.search(text):
                return cat, p.pattern
    return None


def classify(headline: str, exchange_category: str = "", subcategory: str = "") -> dict:
    """Return {category, priority, method, reason}."""
    head, sub = _norm(headline), _norm(subcategory)
    text = " ".join(x for x in (sub, head, _norm(exchange_category)) if x)
    hit = _match(sub, text)
    if hit is None:
        reason = "headline has no content, needs the PDF" if _VAGUE.match(head) else "no rule matched"
        return {"category": "unclassified", "priority": 3, "method": "fallthrough", "reason": reason}

    cat, reason = hit
    prio = PRIORITY[cat]
    if sub.lower() == "board meeting" or _INTIMATION.search(text):
        prio = 2 if cat in _NOTICE_STAYS_P2 else 3
        reason = "board-meeting notice, not the outcome"
    elif cat == "management_change" and not _TOP_ROLE.search(text):
        prio = 2
        reason = "below CXO/board level"
    elif cat == "M&A" and _SUBSIDIARY_SETUP.search(head):
        prio = 2
        reason = "new subsidiary, not a deal"
    return {"category": cat, "priority": prio, "method": "rule", "reason": reason}


def stats(items: list[dict]) -> dict:
    n = len(items) or 1
    by_rule = sum(1 for i in items if i.get("method") == "rule")
    return {"total": len(items), "rule_hit_rate": round(by_rule / n, 3),
            "needs_llm": len(items) - by_rule,
            "needs_pdf": sum(1 for i in items if "needs the PDF" in i.get("reason", ""))}


def _ts(item: dict) -> float:
    return datetime.fromisoformat(item["time"][:19]).timestamp() if item.get("time") else 0.0


def _is_results_filing(item: dict) -> bool:
    return (item.get("bse_subcategory") or "").lower() == "financial results" or \
        bool(re.search(r"\bresults\b", item.get("headline") or "", re.I))


def group_events(items: list[dict], gap_days: float = 3) -> list[dict]:
    """Fold P1/P2 filings into one card per event, most important and newest first.

    Merged: all "results" filings for a company within `gap_days` of each other (the outcome,
    results, presentation and press release), and repeats of an identical headline.
    Other filings stay separate, because two press releases are usually two different stories.
    P3 filings are left out; the digest only shows how many there were.
    """
    gap = gap_days * 86400
    open_events: dict[tuple, dict] = {}
    events: list[dict] = []
    for item in sorted((i for i in items if i["priority"] <= 2), key=_ts):
        key = (item["symbol"], "results") if item["category"] == "results" \
            else (item["symbol"], item["category"], _norm(item["headline"]).lower())
        ev = open_events.get(key)
        if ev is not None and _ts(item) - ev["_last"] <= gap:
            ev["filings"].append(item)
            ev["_last"] = _ts(item)
        else:
            ev = {"_last": _ts(item), "filings": [item]}
            open_events[key] = ev
            events.append(ev)

    out = []
    for ev in events:
        filings = ev["filings"]
        lead = next((f for f in filings if (f.get("bse_subcategory") or "").lower() == "financial results"), None) \
            or next((f for f in filings if _is_results_filing(f)), filings[0])
        priority = min(f["priority"] for f in filings)
        reason = lead["reason"]
        if lead["category"] == "results" and priority == 1 and not any(_is_results_filing(f) for f in filings):
            priority, reason = 2, "board outcome or presentation with no results filing"
        out.append({"symbol": lead["symbol"], "company": lead.get("company"), "category": lead["category"],
                    "priority": priority, "reason": reason, "time": filings[-1].get("time"),
                    "headline": lead["headline"], "pdf": lead.get("pdf"), "filing_count": len(filings),
                    "filing_ids": [f.get("id") for f in filings]})
    out.sort(key=lambda e: (e["priority"], -_ts(e)))
    return out
