import pytest

from research.guidance import actuals, computed_verdict, dedupe, find_quote, finalize, scorecard, supports_target, verify_items

PAGES = ["Opening remarks about the quarter.",
         "We expect double-digit volume\ngrowth in FY27, and margins of 18% to 20% over the medium term.",
         "Thank you all for joining the call today and for the questions."]


def promise(**over):
    return {"metric": "revenue_growth", "statement": "Grow 10%", "quote": "q", "page": 2, "call": "Nov 2025", "target_low": 10.0, "target_high": None, "unit": "pct", **over}


def row(**over):
    return {"promise_id": "P1", "verdict": "delivered", "target_period": "Mar 2026", "actual_ref": "FY:Mar 2026:revenue_growth_pct", "reported_id": None, "explanation": "x", **over}


FIG = {"FY:Mar 2026:revenue_growth_pct": 12.0, "FY:Mar 2026:revenue": 500.0}
EVIDENCE = {"R1": {"statement": "Volumes rose 8%", "quote": "volumes rose", "page": 3, "call": "Feb 2026"}}


def test_quote_found_despite_line_breaks_and_punctuation():
    assert find_quote("we expect double-digit volume growth in FY27", PAGES) == 2


def test_quote_spanning_a_page_break_is_found():
    assert find_quote("medium term. Thank you all for joining", PAGES) == 2


def test_invented_or_too_short_quote_is_rejected():
    assert find_quote("we will double revenue next year guaranteed", PAGES) is None
    assert find_quote("margins", PAGES) is None


def test_verify_drops_invented_items_and_uses_the_real_page():
    items = [{"quote": "margins of 18% to 20% over the medium term", "page": 99}, {"quote": "we promise the moon and the stars", "page": 1}]
    kept, dropped = verify_items(items, PAGES)
    assert dropped == 1 and len(kept) == 1 and kept[0]["page"] == 2


def test_computed_verdicts():
    assert computed_verdict(promise(), 10.0) == "delivered"
    assert computed_verdict(promise(), 9.2) == "partly"
    assert computed_verdict(promise(), 7.0) == "missed"


def test_code_overrides_the_model_when_both_sides_are_numbers():
    out = finalize([row(verdict="missed")], {"P1": promise()}, {}, FIG)
    assert out[0]["verdict"] == "delivered" and out[0]["verdict_source"] == "computed" and out[0]["actual"]["value"] == 12.0


def test_invented_figure_reference_removes_the_verdict():
    out = finalize([row(actual_ref="FY:Mar 2026:made_up_pct")], {"P1": promise()}, {}, FIG)
    assert out[0]["verdict"] == "unmeasurable" and out[0]["actual"] is None


def test_unknown_promise_is_dropped():
    assert finalize([row(promise_id="P9")], {"P1": promise()}, {}, FIG) == []


def test_too_early_is_not_overridden():
    out = finalize([row(verdict="too_early")], {"P1": promise()}, {}, FIG)
    assert out[0]["verdict"] == "too_early"


def test_a_rupee_actual_never_tests_a_percent_target():
    out = finalize([row(verdict="partly", actual_ref="FY:Mar 2026:revenue")], {"P1": promise()}, {}, FIG)
    assert out[0]["verdict"] == "partly" and out[0]["verdict_source"] == "model"


def test_reported_quote_can_be_the_evidence():
    out = finalize([row(actual_ref=None, reported_id="R1", verdict="partly")], {"P1": promise(unit="none", target_low=None)}, EVIDENCE, FIG)
    assert out[0]["reported"]["source"] == "Feb 2026 call p.3" and out[0]["verdict"] == "partly" and out[0]["source"] == "Nov 2025 call p.2"


@pytest.mark.parametrize("quote,target,expected", [
    ("a CAPEX of about Rs. 3,250 crores", 3250.0, True), ("band of 8-  10% for volume", 8.0, True), ("around 7.5% growth", 7.5, True),
    ("I think that is a reasonable take", 5.0, False), ("no number needed here", None, True)])
def test_target_must_appear_in_the_quote(quote, target, expected):
    assert supports_target({"quote": quote, "target_low": target}) is expected


def _margin(call, verdict="delivered"):
    return {"id": call, "metric": "margin", "target_low": 18.0, "target_high": 20.0, "verdict": verdict, "call": call, "repeated": 0}


def test_repeated_guidance_keeps_earliest_tested_and_latest_open():
    out = dedupe([_margin("Feb 2026"), _margin("Nov 2025"), _margin("Jun 2026", "too_early"), _margin("Aug 2026", "too_early")])
    assert [(r["call"], r["repeated"]) for r in out] == [("Nov 2025", 1), ("Aug 2026", 1)]


def test_different_targets_and_unnumbered_promises_are_kept():
    other = {**_margin("Feb 2026"), "target_low": 16.0}
    plain = {**_margin("Feb 2026"), "target_low": None, "target_high": None}
    assert len(dedupe([_margin("Nov 2025"), other, plain, dict(plain)])) == 4


def test_scorecard_counts_only_tested_as_tested():
    s = scorecard([{"verdict": "delivered"}, {"verdict": "missed"}, {"verdict": "too_early"}, {"verdict": "unmeasurable"}])
    assert s["tested"] == 2 and s["too_early"] == 1


def test_actuals_compute_yoy_and_hide_lender_margin():
    q = {"periods": ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026", "Sep 2026"], "rows": {"Sales": [100, 1, 1, 1, 120], "Net Profit": [10, 1, 1, 1, 15], "OPM %": [20, 20, 20, 20, 21]}}
    pl = {"periods": ["Mar 2025", "Mar 2026", "TTM"], "rows": {"Sales": [400, 500, 520], "Net Profit": [40, 50, 52], "OPM %": [20, 21, 21]}}
    company = {"tables": {"quarters": q, "profit_loss": pl}}
    f = actuals(company)
    assert f["Q:Sep 2026:revenue_yoy_pct"] == 20.0 and f["Q:Sep 2026:opm_pct"] == 21 and f["FY:Mar 2026:revenue_growth_pct"] == 25.0 and "TTM" not in str(f)
    lender = {"tables": {"quarters": {"periods": q["periods"], "rows": {"Revenue": q["rows"]["Sales"], "Net Profit": q["rows"]["Net Profit"]}},
                         "profit_loss": {"periods": pl["periods"], "rows": {"Revenue": pl["rows"]["Sales"], "Financing Profit": [1, 1, 1], "Net Profit": pl["rows"]["Net Profit"]}}}}
    assert not any("opm" in k for k in actuals(lender))
