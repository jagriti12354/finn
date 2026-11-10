import json

import pytest
import requests

from ingest.enrich import (MAX_SCANNED_PAGES, _generate, already_verified, keep_better_result, quarter_ends, check_arithmetic, check_screener, ebitda, growth, parse_gemini,
                           score_page, select_pages, summarise, to_crore, verdict)
from ingest.screener import parse_quarters

TABLE = """Statement of Consolidated Financial Results Quarter ended
Revenue from operations 100 Cost of materials 10 Finance costs 5 Depreciation 3
Profit before tax 20 Profit after tax 15 Earnings per share 1"""
NOTES = "Notes: the results for the quarter ended were reviewed by the Audit Committee."


def period(end="2026-09-30", rev=1000.0, other=50.0, total=1050.0, fin=20.0, dep=30.0, exc=None, pbt=200.0, pat=150.0):
    return {"period_end": end, "revenue": rev, "other_income": other, "total_income": total, "finance_costs": fin,
            "depreciation": dep, "exceptional_items": exc, "profit_before_tax": pbt, "profit_after_tax": pat}


def periods(**over):
    return {"current_quarter": period(**over), "prior_quarter": period("2026-06-30"), "year_ago_quarter": period("2025-09-30", rev=800.0, total=850.0)}


def test_page_finder_picks_the_statement_not_notes():
    assert score_page(NOTES) < 5 <= score_page(TABLE)
    assert select_pages(["cover letter", NOTES, TABLE, NOTES]) == ([2], "text")


def test_page_finder_prefers_consolidated():
    standalone = TABLE.replace("Consolidated", "Standalone")
    assert select_pages([standalone, TABLE]) == ([1], "text")


def test_page_finder_falls_back_to_empty_pages_when_scanned():
    assert select_pages(["a covering letter with enough words to count as a real text page", "", " ", ""]) == ([1, 2, 3], "scanned")
    assert len(select_pages([""] * 40)[0]) == MAX_SCANNED_PAGES


def test_units_convert_to_crore_and_nulls_survive():
    raw = {"unit": "million", **{k: {**period(), "exceptional_items": None} for k in ("current_quarter", "prior_quarter", "year_ago_quarter")}}
    p = to_crore(raw)["current_quarter"]
    assert p["revenue"] == 100.0 and p["exceptional_items"] is None and p["period_end"] == "2026-09-30"


def test_ebitda_excludes_other_income_and_exceptionals():
    assert ebitda(period()) == 200.0
    assert ebitda(period(pbt=170.0, exc=-30.0)) == 200.0
    assert ebitda(period(dep=None)) is None


def test_growth_refuses_a_meaningless_base():
    assert growth(120, 100) == 20.0 and growth(120, -5) is None and growth(120, 0) is None and growth(None, 100) is None


def test_summary_has_qoq_yoy_and_drops_ebitda_for_lenders():
    s = summarise(periods(), "Retail")
    assert s["revenue"]["yoy_pct"] == 25.0 and s["revenue"]["qoq_pct"] == 0.0 and "ebitda" in s
    assert "ebitda" not in summarise(periods(), "Banking")


def test_arithmetic_catches_a_misread_digit():
    assert check_arithmetic(periods()) == []
    assert "current_quarter" in check_arithmetic(periods(rev=7000.0))[0]


def _screener(rev=1000, pbt=200, pat=150):
    q = {"Sales": rev, "Profit before tax": pbt, "Net Profit": pat}
    return {"2026-09-30": q, "2026-06-30": q, "2025-09-30": {**q, "Sales": 800}}


def test_verdicts():
    good = check_screener(periods(), _screener())
    assert verdict([], good) == "verified"
    assert verdict([], check_screener(periods(), _screener(pat=170))) == "mismatch"
    assert verdict(["bad sum"], good) == "mismatch"
    assert verdict([], check_screener(periods(), {})) == "unverified"
    assert verdict([], []) == "unverified"


def test_small_rounding_gap_on_revenue_still_matches():
    assert verdict([], check_screener(periods(), _screener(rev=995))) != "mismatch"


def test_revenue_is_net_of_excise_but_arithmetic_uses_gross():
    # ITC, Sep-26: 30578.40 gross, 10309.77 excise, screener shows 20269
    raw = {"unit": "crore", **{k: {**period(rev=30578.40, other=507.36, total=31085.76), "excise_duty": 10309.77}
                               for k in ("current_quarter", "prior_quarter", "year_ago_quarter")}}
    p = to_crore(raw)
    assert p["current_quarter"]["revenue"] == 20268.63 and p["current_quarter"]["gross_revenue"] == 30578.40
    assert check_arithmetic(p) == []


@pytest.mark.parametrize("filed,expected", [
    ("2026-10-15T17:25:00", ("2026-09-30", "2026-06-30", "2025-09-30")),
    ("2027-01-20T12:00:00", ("2026-12-31", "2026-09-30", "2025-12-31")),
    ("2026-05-02T12:00:00", ("2026-03-31", "2025-12-31", "2025-03-31")),
])
def test_quarter_ends_come_from_the_filing_date(filed, expected):
    assert quarter_ends(filed) == expected


class _Resp:
    def __init__(self, code):
        self.status_code, self.ok = code, code == 200
    def json(self):
        return {"answer": 1}
    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


def test_overloaded_model_falls_through_and_winner_moves_first(monkeypatch):
    called = []
    def post(url, **kw):
        called.append(url)
        return _Resp(200 if "good" in url else 503)
    monkeypatch.setattr("ingest.enrich.requests.post", post)
    monkeypatch.setattr("ingest.enrich.time.sleep", lambda s: None)
    models = ["busy", "good"]
    assert _generate({}, models, "k") == ({"answer": 1}, "good") and models == ["good", "busy"]
    assert len(called) == 3  # busy is retried once, then good


def test_hanging_model_falls_through(monkeypatch):
    def post(url, **kw):
        if "hang" in url:
            raise requests.Timeout("slow")
        return _Resp(200)
    monkeypatch.setattr("ingest.enrich.requests.post", post)
    assert _generate({}, ["hang", "good"], "k")[1] == "good"


def test_all_models_hanging_raises(monkeypatch):
    def post(url, **kw):
        raise requests.Timeout("slow")
    monkeypatch.setattr("ingest.enrich.requests.post", post)
    with pytest.raises(requests.Timeout):
        _generate({}, ["a", "b"], "k")


def test_retired_model_is_dropped(monkeypatch):
    monkeypatch.setattr("ingest.enrich.requests.post", lambda url, **kw: _Resp(404 if "old" in url else 200))
    models = ["old", "good"]
    assert _generate({}, models, "k")[1] == "good" and models == ["good"]


def test_already_verified_matches_nearby_events_only():
    saved = {("A", "t"): {"symbol": "A", "event_time": "2026-10-30T16:19:00", "status": "verified"},
             ("B", "t"): {"symbol": "B", "event_time": "2026-10-30T16:19:00", "status": "error"}}
    assert already_verified(saved, {"symbol": "A", "time": "2026-10-30T17:00:00"})
    assert not already_verified(saved, {"symbol": "A", "time": "2027-01-20T17:00:00"})  # next quarter
    assert not already_verified(saved, {"symbol": "B", "time": "2026-10-30T17:00:00"})


def test_error_never_replaces_a_good_result():
    good = {"symbol": "A", "event_time": "t", "status": "verified"}
    saved = {("A", "t"): good}
    keep_better_result(saved, {"symbol": "A", "event_time": "t", "status": "error"})
    assert saved[("A", "t")] is good
    newer = {"symbol": "A", "event_time": "t", "status": "mismatch"}
    keep_better_result(saved, newer)
    assert saved[("A", "t")] is newer


def test_bad_request_does_not_try_other_models(monkeypatch):
    called = []
    monkeypatch.setattr("ingest.enrich.requests.post", lambda url, **kw: called.append(url) or _Resp(400))
    with pytest.raises(RuntimeError):
        _generate({}, ["a", "b"], "k")
    assert len(called) == 1


def test_parse_gemini():
    payload = {"candidates": [{"content": {"parts": [{"text": json.dumps({"unit": "crore"})}]}}],
               "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7}}
    assert parse_gemini(payload) == ({"unit": "crore"}, {"input_tokens": 11, "output_tokens": 7})


def test_parse_screener_quarters():
    page = """<section id="quarters"><table><thead><tr><th></th>
    <th data-date-key="2026-06-30">Jun 2026</th><th data-date-key="2026-09-30">Sep 2026</th></tr></thead><tbody>
    <tr><td class="text"><button>Sales&nbsp;<span>+</span></button></td><td>309,468</td><td>330,601</td></tr>
    <tr><td class="text">Net Profit</td><td>-12</td><td>30</td></tr>
    <tr><td class="text">OPM %</td><td>17%</td><td>18%</td></tr></tbody></table></section>"""
    q = parse_quarters(page)
    assert q["2026-09-30"]["Sales"] == 330601.0 and q["2026-06-30"]["Net Profit"] == -12.0 and "OPM %" not in q["2026-09-30"]
