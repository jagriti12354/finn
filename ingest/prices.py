"""EOD price/volume fetch + configurable unusual-move alerts.

Uses Yahoo's chart JSON directly, with no pandas/yfinance: Windows Smart App Control blocks
pandas' compiled files on the dev laptop, and this step only needs simple arithmetic.

Run:  python -m ingest.prices            # fetch, write data/prices.json + data/price_alerts.json
      python -m ingest.prices --offline  # recompute alerts from cached data only
"""
from __future__ import annotations
import argparse, json, pathlib, time
from datetime import datetime, timezone

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config/portfolio.json").read_text())
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Accept": "application/json",
})
ATTEMPTS = 3
MARKET_TICKER = "^NSEI"  # Nifty 50, to tell a stock's own move from a market-wide one


def parse_chart(payload: dict) -> list[dict]:
    """Yahoo chart JSON -> oldest-first daily bars, skipping days with missing values."""
    result = payload["chart"]["result"][0]
    offset = result["meta"].get("gmtoffset", 0)  # so the date is the exchange's, not UTC's
    quote = result["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(result.get("timestamp") or []):
        values = {k: quote[k][i] for k in ("open", "high", "low", "close", "volume")}
        if any(v is None for v in values.values()):
            continue
        date = datetime.fromtimestamp(ts + offset, tz=timezone.utc).date().isoformat()
        bars.append({"date": date, **{k: float(values[k]) for k in ("open", "high", "low", "close")},
                     "volume": int(values["volume"])})
    return bars


def fetch_symbol(symbol: str, period: str = "6mo", ticker: str | None = None) -> list[dict]:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            r = S.get(CHART_API.format(ticker=ticker or f"{symbol}.NS"),
                      params={"range": period, "interval": "1d"}, timeout=20)
            r.raise_for_status()
            return parse_chart(r.json())
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
            if attempt == ATTEMPTS:
                raise
            time.sleep(attempt)
    return []


def fetch(symbols: list[str], period: str = "6mo") -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Returns (bars by symbol, error by symbol); one bad ticker must not kill the run."""
    out, errors = {}, {}
    for s in symbols:
        try:
            bars = fetch_symbol(s, period)
        except Exception as e:
            errors[s] = str(e)[:120]
            continue
        if bars:
            out[s] = bars
        else:
            errors[s] = "no bars returned"
        time.sleep(0.3)
    return out, errors


def compute_alerts(series: dict[str, list[dict]], volume_multiple: float,
                   lookback: int, price_move_pct: float) -> list[dict]:
    """One alert per breached rule, evaluated on the latest bar.

    Volume baseline excludes the latest day (otherwise a spike inflates its own average).
    """
    alerts = []
    for sym, bars in series.items():
        if len(bars) < lookback + 2:
            continue
        last, prev = bars[-1], bars[-2]
        window = [b["volume"] for b in bars[-(lookback + 1):-1]]
        base = sum(window) / len(window)
        vol_x = last["volume"] / base if base else 0.0
        chg = (last["close"] / prev["close"] - 1) * 100
        ctx = {"symbol": sym, "date": last["date"], "close": round(last["close"], 2),
               "change_pct": round(chg, 2), "volume_x": round(vol_x, 2)}
        if vol_x >= volume_multiple:
            alerts.append({**ctx, "rule": "volume", "headline":
                           f"{sym} volume {vol_x:.1f}x its {lookback}-day average ({chg:+.1f}% on the day)"})
        if abs(chg) >= price_move_pct:
            alerts.append({**ctx, "rule": "price", "headline":
                           f"{sym} {'up' if chg > 0 else 'down'} {abs(chg):.1f}% (threshold ±{price_move_pct}%)"})
    # Most extreme first: combined severity relative to each threshold
    alerts.sort(key=lambda a: -max(a["volume_x"] / volume_multiple, abs(a["change_pct"]) / price_move_pct))
    return alerts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    a = CFG["alerts"]
    syms = [p["symbol"] for p in CFG["portfolio"]]

    if args.offline:
        series = json.loads((DATA / "prices.json").read_text())["series"]
    else:
        series, errors = fetch(syms)
        if not series:
            raise SystemExit(f"No price data fetched; keeping the existing cache. Errors: {errors}")
        try:
            market = fetch_symbol("NIFTY", ticker=MARKET_TICKER)
        except Exception as e:  # alerts still work without it, just not market-relative
            market = []
            errors["NIFTY"] = str(e)[:120]
        (DATA / "prices.json").write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(),
                                                      "series": series, "market": market}))
        if errors:
            print("WARN no data for:", errors)

    alerts = compute_alerts(series, a["volume_multiple"], a["volume_lookback_days"], a["price_move_pct"])
    (DATA / "price_alerts.json").write_text(json.dumps(alerts, indent=1))
    print(f"{len(series)} tickers, {len(alerts)} alerts")
    for al in alerts:
        print(" -", al["headline"])


if __name__ == "__main__":
    main()
