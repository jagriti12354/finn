from ingest.classify import classify, group_events
from ingest.prices import compute_alerts, parse_chart

CASES = [
 ("Outcome of Board Meeting - Unaudited Financial Results for the quarter ended September 30, 2026", "results", 1),
 ("Intimation of Board Meeting to consider Financial Results", "results", 3),
 ("Resignation of Chief Financial Officer", "management_change", 1),
 ("Appointment of Company Secretary and Compliance Officer", "management_change", 2),
 ("Acquisition of 51% stake in XYZ Pvt Ltd", "M&A", 1),
 ("Credit Rating reaffirmed by CRISIL", "credit_rating", 1),
 ("Disclosure under SAST Regulation 29(2)", "insider_trading", 2),
 ("Allotment of shares under ESOP scheme", "unclassified", 3),
 ("Transcript of Earnings Call for Q2 FY27", "earnings_call", 3),
 ("Company bags order worth Rs 1,200 crore", "press_release", 2),
 ("Record date for Interim Dividend", "dividend", 2),
 ("Creation of pledge on promoter shares", "pledging", 1),
 ("Order passed by GST Authority - penalty", "litigation", 1),
]
def test_classifier():
    bad = [(h, classify(h)) for h, c, p in CASES if (classify(h)["category"], classify(h)["priority"]) != (c, p)]
    assert not bad, bad

# Real BSE headlines (Oct-Nov 2026) that the first version of the rules got wrong.
REAL = [
 ("Newspaper Advertisement for the Unaudited Financial Results- September 30, 2026.", "Newspaper Publication", "unclassified", 3),
 ("Copy of Published Results", "General", "unclassified", 3),
 ("Submission of Voting Results and Scrutinizer's Report", "EGM", "agm_egm", 3),
 ("Completion and results of postal ballot dated 01 October 2026", "General", "postal_ballot", 3),
 ("Titan Company Ltdhas informed BSE that the meeting of the Board of Directors of the Company is scheduled on 05/11/2026 ,inter alia, to consider and approve financial results", "Board Meeting", "results", 3),
 ("Redemption of Commercial Paper", "Intimation of Repayment of Commercial Paper (CP)", "unclassified", 3),
 ("Announcement under Regulation 30 of SEBI LODR- ESG Rating by Crisil ESG Ratings & Analytics Ltd.", "General", "unclassified", 3),
 ("Proposed Initial Public Offer of Jio Platforms Limited", "General", "fund_raise", 1),
 ("L&T bags (Significant*) order from Noida Metro for Aqua Line Extension", "", "press_release", 2),
 ("L&T Wins Orders (Large*) in New Delhi and Andhra Pradesh", "", "press_release", 2),
 ("Disclosure under Regulation 30 and other applicable regulations of the SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015 - Change in Managing Director and Chief Executive ....", "General", "management_change", 1),
 ("Incorporation of Wholly Owned Subsidiary", "Acquisition", "M&A", 2),
 ("Scheme of Amalgamation of Sproutlife Foods Private Limited with the Company", "Scheme of Arrangement", "M&A", 1),
 ("Intimation of Grant Of Options Under Angel Broking Employee Long Term Incentive Plan 2021.", "General", "unclassified", 3),
 ("Unaudited Financial Results for the quarter and half year ended 30 September 2026", "Financial Results", "results", 1),
 ("Reference to the Financial Results issued by Suzuki Motor Corporation", "General", "press_release", 2),
]
def test_classifier_on_real_headlines():
    got = [(h, classify(h, "Company Update", s)) for h, s, *_ in REAL]
    bad = [(h, r) for (h, r), (_, _, c, p) in zip(got, REAL) if (r["category"], r["priority"]) != (c, p)]
    assert not bad, bad

def test_vague_headline_flagged_for_pdf():
    assert classify("Please find the attached file.", "Company Update", "General")["reason"] == "headline has no content, needs the PDF"
    assert classify("Change of auditor's fee schedule", "Company Update", "General")["reason"] == "no rule matched"

def _filing(id, symbol, headline, sub, time, category="results", priority=1):
    return {"id": id, "symbol": symbol, "company": symbol, "headline": headline, "bse_subcategory": sub, "time": time,
            "category": category, "priority": priority, "reason": "r", "pdf": None}

def test_results_filings_fold_into_one_event():
    ev = group_events([
        _filing(1, "A", "Outcome of Board Meeting", "Outcome of Board Meeting", "2026-10-30T16:06:00"),
        _filing(2, "A", "Results for the quarter", "Financial Results", "2026-10-30T16:19:00"),
        _filing(3, "A", "Investor Presentation", "Investor Presentation", "2026-10-30T17:00:00"),
        _filing(4, "B", "Results for the quarter", "Financial Results", "2026-10-30T16:19:00"),
    ])
    a = next(e for e in ev if e["symbol"] == "A")
    assert len(ev) == 2 and a["filing_count"] == 3 and a["filing_ids"] == [1, 2, 3] and a["headline"] == "Results for the quarter"

def test_board_outcome_without_results_is_not_p1():
    ev = group_events([_filing(1, "A", "Outcome of the Board Meeting", "Outcome of Board Meeting", "2026-10-12T19:56:00")])
    assert ev[0]["priority"] == 2

def test_different_press_releases_stay_separate_and_p3_is_left_out():
    ev = group_events([
        _filing(1, "A", "Press Release - one", "General", "2026-10-30T10:00:00", "press_release", 2),
        _filing(2, "A", "Press Release - two", "General", "2026-10-30T11:00:00", "press_release", 2),
        _filing(3, "A", "Copy of Published Results", "General", "2026-10-30T12:00:00", "unclassified", 3),
    ])
    assert len(ev) == 2 and {e["filing_ids"][0] for e in ev} == {1, 2}

def test_alerts():
    flat = [{"date": f"2026-09-{d:02d}", "close": 100.0, "volume": 1_000_000} for d in range(1, 31)]
    spike = flat[:-1] + [{"date": "2026-09-30", "close": 105.0, "volume": 2_600_000}]
    al = compute_alerts({"X": spike, "Y": flat}, 2.0, 20, 4.0)
    assert {a["rule"] for a in al} == {"volume", "price"} and all(a["symbol"] == "X" for a in al)

def test_parse_chart_skips_missing_and_uses_exchange_date():
    # 2026-11-09 18:45 UTC is 2026-11-10 00:15 in IST
    payload = {"chart": {"result": [{"meta": {"gmtoffset": 19800}, "timestamp": [1794168000, 1794249900],
        "indicators": {"quote": [{"open": [None, 10], "high": [None, 12], "low": [None, 9],
                                  "close": [None, 11], "volume": [None, 500]}]}}]}}
    assert parse_chart(payload) == [{"date": "2026-11-10", "open": 10.0, "high": 12.0, "low": 9.0,
                                     "close": 11.0, "volume": 500}]
