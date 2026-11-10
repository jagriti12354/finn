"""Number-based research sections for any ticker: snapshot, trajectory, balance sheet, cash quality.

Plain arithmetic on the standardised financials. No model is involved, so these sections cost nothing and
cannot invent a figure. Each section's `signals` are fixed rules that say what is growing or compressing.

Run:  python -m research.numbers ASIANPAINT
"""
from __future__ import annotations
import json, pathlib, sys
from datetime import date

from ingest.screener import fetch_company

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "company"
SOURCE = "Screener standardised financials"


def load_company(symbol: str) -> dict:
    """Fetched at most once a day per ticker."""
    symbol = symbol.strip().upper()
    path = CACHE / f"{symbol.replace('&', '_')}-{date.today().isoformat()}.json"
    if path.exists():
        return json.loads(path.read_text())
    company = fetch_company(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(company))
    return company


def row(table: dict, *names: str) -> list:
    for name in names:
        for label, values in table["rows"].items():
            if label == name or label.startswith(name + " "):
                return values
    return [None] * len(table["periods"])


def annual(table: dict, *names: str) -> tuple[list[str], list]:
    """(periods, values) for full financial years only, dropping TTM and half-year columns."""
    values = row(table, *names)
    keep = [i for i, p in enumerate(table["periods"]) if p != "TTM"]
    march = [i for i in keep if table["periods"][i].startswith("Mar")]
    keep = march if len(march) >= len(keep) - 1 and march else keep
    return [table["periods"][i] for i in keep], [values[i] for i in keep]


def latest(values: list):
    return next((v for v in reversed(values) if v is not None), None)


def cagr(values: list, years: int) -> float | None:
    """Compound growth over the last `years`. None when either end isn't positive, where it means nothing."""
    if len(values) <= years or values[-1] is None or values[-1 - years] is None:
        return None
    start, end = values[-1 - years], values[-1]
    if start <= 0 or end <= 0:
        return None
    return round(((end / start) ** (1 / years) - 1) * 100, 1)


def growth(current, base) -> float | None:
    if current is None or base is None or base <= 0:
        return None
    return round((current / base - 1) * 100, 1)


def ratio(top, bottom) -> float | None:
    return None if top is None or not bottom else round(top / bottom, 2)


def total(values: list) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def signal(tone: str, text: str) -> dict:
    return {"tone": tone, "text": text}


def is_financial(company: dict) -> bool:
    return any(l.startswith("Financing Profit") for l in company["tables"]["profit_loss"]["rows"])


def _sales(table: dict) -> list:
    return row(table, "Sales", "Revenue")


def _profit_label(company: dict) -> tuple[str, str]:
    return ("Financing Profit", "Financing Margin") if is_financial(company) else ("Operating Profit", "OPM")


def snapshot(company: dict) -> dict:
    pl, q, top = company["tables"]["profit_loss"], company["tables"]["quarters"], company["top_ratios"]
    profit, margin = _profit_label(company)
    ttm = pl["periods"].index("TTM") if "TTM" in pl["periods"] else -1
    sales_q, net_q, opm_q = _sales(q), row(q, "Net Profit"), row(q, margin)
    period = q["periods"][-1] if q["periods"] else None
    def yoy(series):
        return growth(series[-1], series[-5]) if len(series) >= 5 else None
    lender = is_financial(company)  # a lender's "financing margin" is not an operating margin, so it isn't shown
    return {"title": "Financial snapshot", "source": SOURCE, "basis": company["basis"],
            "market": {k: top.get(k) for k in ("Market Cap", "Current Price", "Stock P/E", "ROCE", "ROE", "Dividend Yield")},
            "ttm": {"revenue": _sales(pl)[ttm], "operating_profit": None if lender else row(pl, profit)[ttm], "pat": row(pl, "Net Profit")[ttm],
                    "margin_pct": None if lender else row(pl, margin)[ttm]},
            "latest_quarter": {"period": period, "revenue": sales_q[-1] if sales_q else None, "revenue_yoy_pct": yoy(sales_q),
                               "pat": net_q[-1] if net_q else None, "pat_yoy_pct": yoy(net_q),
                               "margin_pct": None if lender or not opm_q else opm_q[-1]}}


def trajectory(company: dict) -> dict:
    pl = company["tables"]["profit_loss"]
    profit, margin = _profit_label(company)
    periods, sales = annual(pl, "Sales", "Revenue")
    _, op = annual(pl, profit)
    _, net = annual(pl, "Net Profit")
    _, margins = annual(pl, margin)
    if is_financial(company):  # same reason as the snapshot: these two don't mean the same thing for a lender
        op = [None] * len(op)
        margins = [None] * len(margins)
    span = {"3y": 3, "5y": 5, "10y": 10}
    table = {name: {k: cagr(series, n) for k, n in span.items()} for name, series in (("revenue", sales), ("operating_profit", op), ("pat", net))}

    signals = []
    r3, r5 = table["revenue"]["3y"], table["revenue"]["5y"]
    if r3 is not None and r5 is not None and abs(r3 - r5) >= 3:
        signals.append(signal("positive" if r3 > r5 else "negative", f"Revenue growth {'accelerating' if r3 > r5 else 'slowing'}: {r3}% a year over 3 years against {r5}% over 5."))
    if len(margins) >= 4 and margins[-1] is not None and margins[-4] is not None and abs(margins[-1] - margins[-4]) >= 1.5:
        move = margins[-1] - margins[-4]
        signals.append(signal("positive" if move > 0 else "negative", f"Margin {'expanding' if move > 0 else 'compressing'}: {margins[-4]:.0f}% to {margins[-1]:.0f}% in 3 years."))
    p3 = table["pat"]["3y"]
    if r3 is not None and p3 is not None and abs(p3 - r3) >= 5:
        signals.append(signal("positive" if p3 > r3 else "negative", f"Profit {'outrunning' if p3 > r3 else 'lagging'} revenue: {p3}% a year against {r3}% over 3 years."))
    return {"title": "Trajectory", "source": SOURCE, "basis": company["basis"], "cagr_pct": table,
            "series": {"periods": periods[-7:], "revenue": sales[-7:], "operating_profit": op[-7:], "pat": net[-7:], "margin_pct": margins[-7:]},
            "signals": signals}


def balance_sheet(company: dict) -> dict:
    bs, ratios = company["tables"]["balance_sheet"], company["tables"]["ratios"]
    financial = is_financial(company)
    periods, debt = annual(bs, "Borrowings", "Borrowing")
    _, capital = annual(bs, "Equity Capital")
    _, reserves = annual(bs, "Reserves")
    equity = [None if c is None or r is None else c + r for c, r in zip(capital, reserves)]
    de = [ratio(d, e) for d, e in zip(debt, equity)]
    out = {"title": "Balance sheet", "source": SOURCE, "basis": company["basis"], "is_financial": financial,
           "latest": {"period": bs["periods"][-1] if bs["periods"] else None, "borrowings": latest(row(bs, "Borrowings", "Borrowing")),
                      "deposits": latest(row(bs, "Deposits")), "cwip": latest(row(bs, "CWIP")), "investments": latest(row(bs, "Investments"))},
           "series": {"periods": periods[-7:], "borrowings": debt[-7:], "debt_to_equity": de[-7:]}, "signals": []}
    if financial:  # borrowing is a lender's raw material, so leverage alarms would mislead
        out["signals"].append(signal("neutral", "Lender: borrowings fund the loan book, so debt-to-equity is read differently."))
        return out
    if len(de) >= 4 and de[-1] is not None and de[-4] is not None and abs(de[-1] - de[-4]) >= 0.2:
        out["signals"].append(signal("negative" if de[-1] > de[-4] else "positive", f"Leverage {'rising' if de[-1] > de[-4] else 'falling'}: debt-to-equity {de[-4]} to {de[-1]} in 3 years."))
    elif de and de[-1] is not None and de[-1] < 0.1:
        out["signals"].append(signal("positive", f"Almost no debt: debt-to-equity {de[-1]}."))
    r_periods, ccc = annual(ratios, "Cash Conversion Cycle")
    out["working_capital"] = {"periods": r_periods[-7:], "cash_conversion_days": ccc[-7:], "debtor_days": annual(ratios, "Debtor Days")[1][-7:],
                              "inventory_days": annual(ratios, "Inventory Days")[1][-7:], "payable_days": annual(ratios, "Days Payable")[1][-7:]}
    if len(ccc) >= 4 and ccc[-1] is not None and ccc[-4] is not None and abs(ccc[-1] - ccc[-4]) >= 10:
        worse = ccc[-1] > ccc[-4]
        out["signals"].append(signal("negative" if worse else "positive", f"Working-capital cycle {'lengthening' if worse else 'shortening'}: {ccc[-4]:.0f} to {ccc[-1]:.0f} days in 3 years."))
    return out


def cash_quality(company: dict) -> dict:
    if is_financial(company):
        return {"title": "Cash quality", "source": SOURCE, "basis": company["basis"], "applicable": False,
                "signals": [signal("neutral", "Not meaningful for a lender: loans issued count as operating cash outflow.")]}
    cf, pl = company["tables"]["cash_flow"], company["tables"]["profit_loss"]
    periods, cfo = annual(cf, "Cash from Operating Activity")
    _, fcf = annual(cf, "Free Cash Flow")
    pl_periods, op = annual(pl, "Operating Profit")
    _, net = annual(pl, "Net Profit")
    by_year_op = dict(zip(pl_periods, op))
    by_year_net = dict(zip(pl_periods, net))
    last5 = periods[-5:]
    cfo5, op5, net5 = total(cfo[-5:]), total([by_year_op.get(p) for p in last5]), total([by_year_net.get(p) for p in last5])
    to_ebitda = None if ratio(cfo5, op5) is None else round(ratio(cfo5, op5) * 100)
    to_pat = None if ratio(cfo5, net5) is None else round(ratio(cfo5, net5) * 100)
    signals = []
    if to_ebitda is not None:
        if to_ebitda < 60:
            signals.append(signal("negative", f"Weak cash conversion: {to_ebitda}% of 5-year operating profit became operating cash."))
        elif to_ebitda >= 85:
            signals.append(signal("positive", f"Strong cash conversion: {to_ebitda}% of 5-year operating profit became operating cash."))
    fcf5 = total(fcf[-5:])
    if fcf5 is not None and fcf5 < 0:
        signals.append(signal("negative", f"Free cash flow negative over 5 years: ₹{fcf5:,.0f} cr in total."))
    return {"title": "Cash quality", "source": SOURCE, "basis": company["basis"], "applicable": True,
            "five_year": {"cfo": cfo5, "free_cash_flow": fcf5, "cfo_to_operating_profit_pct": to_ebitda, "cfo_to_pat_pct": to_pat},
            "series": {"periods": periods[-7:], "cfo": cfo[-7:], "free_cash_flow": fcf[-7:]}, "signals": signals}


def build(symbol: str) -> dict:
    company = load_company(symbol)
    return {"symbol": company["symbol"], "name": company["name"], "basis": company["basis"], "is_financial": is_financial(company),
            "sections": {"snapshot": snapshot(company), "trajectory": trajectory(company),
                         "balance_sheet": balance_sheet(company), "cash_quality": cash_quality(company)},
            "documents": {"concalls": company["documents"]["concalls"][:6], "annual_reports": company["documents"]["annual_reports"][:3]}}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(build(sys.argv[1]), indent=1, ensure_ascii=False))
