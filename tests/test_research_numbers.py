import pytest

from ingest.screener import CompanyNotFound, fetch_company, parse_company, parse_documents, parse_table, parse_top_ratios
from research.numbers import annual, balance_sheet, cagr, cash_quality, growth, is_financial, snapshot, trajectory

YEARS = ["Mar 2021", "Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026"]


def table(periods, **rows):
    return {"periods": periods, "rows": {k.replace("_", " "): v for k, v in rows.items()}}


def company(financial=False):
    profit = "Financing Profit" if financial else "Operating Profit"
    margin = "Financing Margin %" if financial else "OPM %"
    pl = {"periods": YEARS + ["TTM"], "rows": {
        ("Revenue" if financial else "Sales"): [100, 110, 121, 133, 160, 200, 210],
        profit: [20, 22, 24, 20, 22, 26, 28], margin: [20, 20, 20, 15, 14, 13, 13],
        "Net Profit": [10, 11, 12, 13, 14, 15, 16]}}
    q = {"periods": ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026", "Sep 2026"], "rows": {
        "Sales": [50, 51, 52, 53, 60], "Net Profit": [5, 5, 5, 5, 4], "OPM %": [13, 13, 13, 13, 12]}}
    bs = table(YEARS + ["Sep 2026"], Equity_Capital=[10] * 7, Reserves=[90] * 7, Borrowings=[5, 5, 10, 20, 40, 60, 62], CWIP=[1] * 7, Investments=[2] * 7)
    cf = table(YEARS, Cash_from_Operating_Activity=[5, 5, 5, 5, 5, 5], Free_Cash_Flow=[-1, -1, -1, -1, -1, -1])
    r = table(YEARS, Cash_Conversion_Cycle=[20, 20, 20, 30, 40, 50], Debtor_Days=[1] * 6, Inventory_Days=[1] * 6, Days_Payable=[1] * 6)
    return {"symbol": "X", "name": "X Ltd", "basis": "consolidated", "top_ratios": {"ROCE": 20.0}, "documents": {"concalls": [], "annual_reports": []},
            "tables": {"profit_loss": pl, "quarters": q, "balance_sheet": bs, "cash_flow": cf, "ratios": r}}


def tones(section):
    return [s["tone"] for s in section["signals"]]


def test_cagr_needs_positive_ends_and_enough_years():
    assert cagr([100, 110, 121], 2) == 10.0 and cagr([-5, 10, 20], 2) is None and cagr([1, 2], 5) is None


def test_annual_drops_ttm_and_half_year_columns():
    c = company()
    assert annual(c["tables"]["profit_loss"], "Sales")[0] == YEARS
    assert annual(c["tables"]["balance_sheet"], "Borrowings")[0] == YEARS


def test_snapshot_uses_ttm_and_year_ago_quarter():
    s = snapshot(company())
    assert s["ttm"]["revenue"] == 210 and s["latest_quarter"]["revenue_yoy_pct"] == 20.0 and s["latest_quarter"]["pat_yoy_pct"] == -20.0


def test_trajectory_flags_acceleration_and_compression():
    t = trajectory(company())
    assert any("accelerating" in s["text"] for s in t["signals"]) or t["cagr_pct"]["revenue"]["3y"] is not None
    assert any("compressing" in s["text"] and s["tone"] == "negative" for s in t["signals"])


def test_balance_sheet_flags_rising_debt_and_longer_cycle():
    b = balance_sheet(company())
    assert b["series"]["debt_to_equity"][-1] == 0.6
    assert any("Leverage rising" in s["text"] for s in b["signals"]) and any("lengthening" in s["text"] for s in b["signals"])


def test_cash_quality_flags_weak_conversion_and_negative_fcf():
    c = cash_quality(company())
    assert c["five_year"]["cfo_to_operating_profit_pct"] < 60 and tones(c) == ["negative", "negative"]


def test_lenders_skip_leverage_and_cash_alarms():
    c = company(financial=True)
    assert is_financial(c) and cash_quality(c)["applicable"] is False
    assert tones(balance_sheet(c)) == ["neutral"]
    s = snapshot(c)
    assert s["ttm"]["revenue"] == 210 and s["ttm"]["operating_profit"] is None and s["ttm"]["margin_pct"] is None
    assert not any("Margin" in x["text"] for x in trajectory(c)["signals"])


def test_growth_refuses_a_bad_base():
    assert growth(10, -2) is None and growth(120, 100) == 20.0


PAGE = """<h1 class="x">Asian Paints Ltd</h1>
<ul id="top-ratios"><li><span class="name">Market Cap</span> ₹ <span>2,40,692</span> Cr.</li><li><span class="name">Stock P/E</span> <span>48.6</span></li></ul>
<section id="profit-loss"><table><thead><tr><th></th><th>Mar 2025</th><th>TTM</th></tr></thead><tbody>
<tr><td><button>Sales&nbsp;<span>+</span></button></td><td>1,000</td><td>1,100</td></tr><tr><td>OPM %</td><td>18%</td><td></td></tr></tbody></table></section>
<section id="documents"><div class="documents annual-reports x"><ul><li><a href="https://b/ar.pdf" class="y">Annual Report 2026</a><button>Summary</button></li></ul></div>
<div class="documents credit-ratings"></div><div class="documents concalls x"><ul>
<li><div style="width: 74px">Nov 2026</div><a class="c" href="https://b/t.pdf" title="Raw Transcript">Transcript</a><button data-url="/concalls/summary/1/">AI Summary</button></li>
<li><div style="width: 74px">Aug 2026</div><a href="https://b/ppt.pdf">PPT</a></li></ul></section>"""


def test_parse_table_handles_percent_and_blank_cells():
    t = parse_table(PAGE, "profit-loss")
    assert t["periods"] == ["Mar 2025", "TTM"] and t["rows"]["Sales"] == [1000.0, 1100.0] and t["rows"]["OPM %"] == [18.0, None]
    assert parse_table(PAGE, "missing") == {"periods": [], "rows": {}}


def test_parse_top_ratios():
    assert parse_top_ratios(PAGE) == {"Market Cap": 240692.0, "Stock P/E": 48.6}


def test_parse_documents_keeps_primary_sources_only():
    d = parse_documents(PAGE)
    assert d["concalls"] == [{"period": "Nov 2026", "url": "https://b/t.pdf"}]
    assert d["annual_reports"] == [{"title": "Annual Report 2026", "url": "https://b/ar.pdf"}]
    assert "summary" not in str(d).lower()


def test_parse_company_name():
    assert parse_company(PAGE, "ASIANPAINT", "consolidated")["name"] == "Asian Paints Ltd"


@pytest.mark.parametrize("bad", ["../etc/passwd", "A B", "", "X" * 40])
def test_bad_tickers_are_rejected_before_any_request(bad, monkeypatch):
    monkeypatch.setattr("ingest.screener.requests.get", lambda *a, **k: pytest.fail("made a request"))
    with pytest.raises(CompanyNotFound):
        fetch_company(bad)
