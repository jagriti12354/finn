"""Corporate announcements from BSE's public JSON API -> classified, prioritised feed.

Why BSE and not NSE: NSE's site needs cookie-priming and blocks most scripted/cloud traffic;
BSE's announcement API is plain JSON with a Referer header. Every NSE-listed large/mid cap is
also on BSE, so coverage is the same.

Run:  python -m ingest.announcements --days 3
      python -m ingest.announcements --verify      # check every BSE code resolves to the right company
"""
from __future__ import annotations
import argparse, json, pathlib, time
from datetime import datetime, timedelta

import requests

from ingest.classify import classify, group_events, stats

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config/portfolio.json").read_text())
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
PDF_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
}
S = requests.Session()
S.headers.update(HEADERS)


def _get_table(params: dict, attempts: int = 3) -> list[dict]:
    """BSE occasionally answers a valid request with a 400, and a retry succeeds."""
    for attempt in range(1, attempts + 1):
        try:
            r = S.get(API, params=params, timeout=20)
            r.raise_for_status()
            return r.json().get("Table", [])
        except (requests.RequestException, ValueError):
            if attempt == attempts:
                raise
            time.sleep(attempt)
    return []


def fetch_scrip(bse_code: str, start: datetime, end: datetime) -> list[dict]:
    rows, page = [], 1
    while page <= 10:
        params = {"pageno": page, "strCat": "-1", "strPrevDate": start.strftime("%Y%m%d"),
                  "strScrip": bse_code, "strSearch": "P", "strToDate": end.strftime("%Y%m%d"),
                  "strType": "C", "subcategory": "-1"}
        table = _get_table(params)
        if not table:
            break
        rows.extend(table)
        if len(table) < 50:  # BSE pages are 50 rows
            break
        page += 1
        time.sleep(0.4)
    return rows


def normalise(row: dict, symbol: str) -> dict:
    headline = (row.get("HEADLINE") or row.get("NEWSSUB") or "").strip()
    c = classify(headline, row.get("CATEGORYNAME", ""), row.get("SUBCATNAME", ""))
    att = row.get("ATTACHMENTNAME")
    return {
        "id": row.get("NEWSID"),
        "symbol": symbol,
        "company": row.get("SLONGNAME"),
        "time": row.get("DT_TM") or row.get("NEWS_DT"),
        "subject": (row.get("NEWSSUB") or "").strip(),
        "headline": headline,
        "bse_category": row.get("CATEGORYNAME"),
        "bse_subcategory": row.get("SUBCATNAME"),
        "pdf": PDF_BASE + att if att else None,
        **c,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    end = datetime.now()
    start = end - timedelta(days=args.days if not args.verify else 60)

    feed, errors = [], []
    for p in CFG["portfolio"]:
        try:
            rows = fetch_scrip(p["bse"], start, end)
        except Exception as e:  # keep going; one bad ticker must not kill the digest
            errors.append(f"{p['symbol']}: {e}")
            continue
        if args.verify:
            names = {r.get("SLONGNAME") for r in rows}
            print(f"{p['symbol']:<11} {p['bse']}  {len(rows):>3} filings  -> {names or 'NO FILINGS (check code)'}")
            continue
        feed.extend(normalise(r, p["symbol"]) for r in rows)
        time.sleep(0.4)

    if args.verify:
        return
    if errors and not feed:  # an outage must not replace the saved feed with an empty one
        raise SystemExit(f"No filings fetched; keeping the existing feed. Errors: {errors[:3]}")
    feed.sort(key=lambda x: (x["priority"], -(datetime.fromisoformat(x["time"][:19]).timestamp() if x["time"] else 0)))
    events = group_events(feed)
    (DATA / "announcements.json").write_text(json.dumps(
        {"fetched_at": end.isoformat(), "window_days": args.days, "events": events, "items": feed}, indent=1))
    print(json.dumps(stats(feed)), "| events:", len(events), "| errors:", errors or "none")
    for ev in events[:15]:
        print(f" P{ev['priority']} {ev['category']:<17} {ev['symbol']:<10} x{ev['filing_count']} {ev['headline'][:80]}")
    misses = [i["headline"] for i in feed if i["method"] != "rule"]
    if misses:
        print("\nUnmatched (tune rules or send to LLM):")
        for m in misses[:20]:
            print("  ?", m[:100])


if __name__ == "__main__":
    main()
