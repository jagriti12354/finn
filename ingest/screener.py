"""Quarterly table from a screener.in company page, used to cross-check filing numbers.

Parsed with the standard library so nothing here needs a compiled package.
"""
from __future__ import annotations
import html, re
from urllib.parse import quote

import requests

URL = {"consolidated": "https://www.screener.in/company/{symbol}/consolidated/",
       "standalone": "https://www.screener.in/company/{symbol}/"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}


def _clean(cell: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", cell)).replace("\xa0", " ")
    return " ".join(text.split()).rstrip("+ ").strip()


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def parse_quarters(page: str) -> dict[str, dict[str, float]]:
    """HTML -> {"2026-09-30": {"Sales": 330601.0, "Net Profit": ...}}, in ₹ crore."""
    section = re.search(r'<section id="quarters".*?</section>', page, re.S)
    if not section:
        return {}
    table = re.search(r"<table.*?</table>", section.group(0), re.S)
    if not table:
        return {}
    dates = re.findall(r'data-date-key="(\d{4}-\d{2}-\d{2})"', table.group(0))
    out: dict[str, dict[str, float]] = {d: {} for d in dates}
    body = re.search(r"<tbody>(.*?)</tbody>", table.group(0), re.S)
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1) if body else "", re.S):
        cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) != len(dates) + 1:
            continue
        for d, cell in zip(dates, cells[1:]):
            value = _number(cell)
            if value is not None:
                out[d][cells[0]] = value
    return out


def fetch_quarters(symbol: str, basis: str = "consolidated") -> dict[str, dict[str, float]]:
    r = requests.get(URL[basis].format(symbol=symbol), headers=HEADERS, timeout=30)
    r.raise_for_status()
    return parse_quarters(r.text)


class CompanyNotFound(Exception):
    pass


TOP_RATIOS = ("Market Cap", "Current Price", "Stock P/E", "Book Value", "Dividend Yield", "ROCE", "ROE")
TABLES = {"quarters": "quarters", "profit_loss": "profit-loss", "balance_sheet": "balance-sheet",
          "cash_flow": "cash-flow", "ratios": "ratios", "shareholding": "shareholding"}
_SYMBOL = re.compile(r"^[A-Z0-9&_.-]{1,20}$")


def _cell_number(text: str) -> float | None:
    return _number(text.rstrip("% ").strip())


def parse_table(page: str, section_id: str) -> dict:
    """A screener table -> {"periods": [...], "rows": {label: [value or None, ...]}}."""
    section = re.search(rf'<section id="{section_id}".*?</section>', page, re.S)
    table = re.search(r"<table.*?</table>", section.group(0), re.S) if section else None
    if not table:
        return {"periods": [], "rows": {}}
    periods = [_clean(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", table.group(0), re.S)][1:]
    rows: dict[str, list] = {}
    body = re.search(r"<tbody>(.*?)</tbody>", table.group(0), re.S)
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1) if body else "", re.S):
        cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) == len(periods) + 1:
            rows[cells[0]] = [_cell_number(c) for c in cells[1:]]
    return {"periods": periods, "rows": rows}


def parse_top_ratios(page: str) -> dict[str, float]:
    block = re.search(r'<ul id="top-ratios".*?</ul>', page, re.S)
    out = {}
    for item in re.findall(r"<li.*?</li>", block.group(0) if block else "", re.S):
        text = _clean(item)
        name = next((n for n in TOP_RATIOS if text.startswith(n)), None)
        value = re.search(r"-?\d[\d,]*(?:\.\d+)?", text[len(name):]) if name else None
        if value:
            out[name] = float(value.group(0).replace(",", ""))
    return out


def parse_documents(page: str) -> dict[str, list[dict]]:
    """Links to primary documents only. Screener's own AI summaries are deliberately ignored."""
    concalls, annual = [], []
    block = re.search(r'class="documents concalls.*?(?=</section>)', page, re.S)
    for item in re.findall(r"<li.*?</li>", block.group(0) if block else "", re.S):
        when = re.search(r">\s*([A-Z][a-z]{2} \d{4})\s*</div>", item)
        link = re.search(r'<a[^>]*href="([^"]+)"[^>]*title="Raw Transcript"', item)
        if when and link:
            concalls.append({"period": when.group(1), "url": html.unescape(link.group(1))})
    block = re.search(r'class="documents annual-reports.*?(?=class="documents )', page, re.S)
    for item in re.findall(r"<li.*?</li>", block.group(0) if block else "", re.S):
        link = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', item, re.S)
        if link:
            annual.append({"title": _clean(link.group(2)), "url": html.unescape(link.group(1))})
    return {"concalls": concalls, "annual_reports": annual}


def parse_company(page: str, symbol: str, basis: str) -> dict:
    name = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    return {"symbol": symbol, "name": _clean(name.group(1)) if name else symbol, "basis": basis,
            "top_ratios": parse_top_ratios(page), "documents": parse_documents(page),
            "tables": {key: parse_table(page, sid) for key, sid in TABLES.items()}}


def fetch_company(symbol: str) -> dict:
    """Any listed company by NSE/BSE code. Falls back to standalone when there are no consolidated accounts."""
    symbol = symbol.strip().upper()
    if not _SYMBOL.match(symbol):
        raise CompanyNotFound(f"Not a valid ticker: {symbol[:30]}")
    quoted = quote(symbol, safe="")
    found = False
    for basis in ("consolidated", "standalone"):
        r = requests.get(URL[basis].format(symbol=quoted), headers=HEADERS, timeout=30)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        found = True
        company = parse_company(r.text, symbol, basis)
        if company["tables"]["profit_loss"]["rows"]:
            return company
    raise CompanyNotFound(f"No data for {symbol}" if found else f"Unknown ticker: {symbol}")
