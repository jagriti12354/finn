"""Push: turn filings and price moves into a few Telegram pings and one morning digest.

Three lanes:
  ping    worth interrupting for, capped per day; overflow drops to the digest
  digest  the other P1/P2 cards, sent once each morning
  silent  P3 filings, only counted as "routine (N)"

A price move and a filing close together become one card. Timing is all we know, so the wording
says when a filing was made and never that it caused the move.

Run:  python -m ingest.push --dry-run                     # show what would be sent; sends and saves nothing
      python -m ingest.push                               # send new pings
      python -m ingest.push --digest                      # send the morning digest
      python -m ingest.push --dry-run --as-of 2026-11-05T17:00   # replay a past moment
"""
from __future__ import annotations
import argparse, html, json, os, pathlib, re
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from ingest.biotext import lines_for
from ingest.prices import compute_alerts

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config/portfolio.json").read_text())
DATA = ROOT / "data"
STATE_FILE = DATA / "push_state.json"

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_CLOSE = (15, 45)  # today's bar is partial before this, so it isn't used
TELEGRAM = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE = 3500  # Telegram's limit is 4096

BASE_SCORE = {"pledging": 90, "credit_rating": 85, "results": 80, "M&A": 75, "management_change": 75, "move": 60}
_NEGATIVE_RATING = re.compile(r"downgrad|negative|credit watch|rating watch", re.I)
LABEL = {"results": "Results", "M&A": "Deal", "management_change": "Management change", "fund_raise": "Fund raise",
         "litigation": "Litigation", "credit_rating": "Credit rating", "pledging": "Pledge", "capex": "Capex",
         "insider_trading": "Insider/SAST", "dividend": "Dividend", "bonus_split": "Bonus/split", "press_release": "Update"}
METRICS = (("revenue", "Revenue"), ("ebitda", "EBITDA"), ("pbt", "PBT"), ("pat", "PAT"))


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp[:19])


def _pct(value: float | None) -> str:
    return "n.m." if value is None else f"{round(value, 1) + 0.0:+.1f}%"  # + 0.0 turns -0.0 into 0.0


def _day(date: str) -> str:
    d = datetime.fromisoformat(date[:10])
    return f"{d.day} {d:%b}"


def fy_label(period_end: str) -> str:
    year, month = int(period_end[:4]), int(period_end[5:7])
    quarter = {6: 1, 9: 2, 12: 3, 3: 4}[month]
    return f"Q{quarter} FY{(year + 1 if month > 3 else year) % 100:02d}"


def latest_session(series: dict[str, list[dict]], now: datetime) -> str | None:
    """Newest session with a complete bar at `now`."""
    today = now.date().isoformat()
    dates = {b["date"] for bars in series.values() for b in bars if b["date"] <= today}
    if today in dates and (now.hour, now.minute) < MARKET_CLOSE:
        dates.discard(today)
    return max(dates) if dates else None


def previous_session(series: dict[str, list[dict]], date: str) -> str:
    earlier = {b["date"] for bars in series.values() for b in bars if b["date"] < date}
    return max(earlier) if earlier else date


def moves_for(series: dict[str, list[dict]], market: list[dict], date: str, alerts: dict) -> list[dict]:
    """One record per stock that breached a threshold on `date`, with its move relative to Nifty."""
    cut = {s: [b for b in bars if b["date"] <= date] for s, bars in series.items()}
    cut = {s: bars for s, bars in cut.items() if bars and bars[-1]["date"] == date}
    index = [b for b in market if b["date"] <= date]
    market_change = None
    if len(index) >= 2 and index[-1]["date"] == date:
        market_change = (index[-1]["close"] / index[-2]["close"] - 1) * 100

    merged: dict[str, dict] = {}
    pct, multiple = alerts["price_move_pct"], alerts["volume_multiple"]
    for a in compute_alerts(cut, multiple, alerts["volume_lookback_days"], pct):
        move = merged.setdefault(a["symbol"], {"symbol": a["symbol"], "date": a["date"], "close": a["close"],
                                               "change_pct": a["change_pct"], "volume_x": a["volume_x"], "rules": []})
        move["rules"].append(a["rule"])
    for move in merged.values():
        relative = round(move["change_pct"] - (market_change or 0.0), 2)
        move.update({"market_change_pct": None if market_change is None else round(market_change, 2),
                     "relative_pct": relative, "price_breach": abs(relative) >= pct,
                     "severity": round(max(abs(relative) / pct, move["volume_x"] / multiple), 2)})
    return list(merged.values())


def event_key(event: dict) -> str:
    return f'{event["symbol"]}:{event["category"]}:{event["filing_ids"][0]}'


def linked_event(move: dict, events: list[dict], previous: str, now: datetime) -> dict | None:
    """A filing the market could have reacted to: made after the previous close and by this session's close.

    Prefers results, then higher priority. A filing after this close came later than the move, so it can't be linked.
    """
    start, end = _dt(f"{previous}T15:30:00"), _dt(f'{move["date"]}T15:30:00')
    near = [e for e in events if e["symbol"] == move["symbol"] and start < _dt(e["time"]) <= min(end, now)]
    return min(near, key=lambda e: (e["category"] != "results", e["priority"], -_dt(e["time"]).timestamp())) if near else None


def result_for(event: dict, results: list[dict]) -> dict | None:
    if event["category"] != "results":
        return None
    near = [r for r in results if r["symbol"] == event["symbol"] and abs((_dt(r["event_time"]) - _dt(event["time"])).days) <= 3]
    return max(near, key=lambda r: r["event_time"]) if near else None


def _name(symbol: str) -> str:
    return next((p["name"] for p in CFG["portfolio"] if p["symbol"] == symbol), symbol)


def _move_text(move: dict, event_date: str | None = None) -> str:
    when = f"Next session ({_day(move['date'])})" if event_date and event_date < move["date"] else f"{_day(move['date'])}"
    market = "" if move["market_change_pct"] is None else f", Nifty {_pct(move['market_change_pct'])}"
    return f"{when}: {_pct(move['change_pct'])} on {move['volume_x']:.1f}x volume{market}"


def filing_card(event: dict, results: list[dict], bios: dict | None = None) -> dict:
    lines, verified = [], False
    bio_lines = lines_for(bios[event_key(event)]) if bios and event_key(event) in bios else []
    title = f'{_name(event["symbol"])} · {LABEL.get(event["category"], event["category"])}'
    if event["category"] == "results":
        result = result_for(event, results)
        if result and result.get("status") == "verified":
            verified = True
            title = f'{_name(event["symbol"])} · {fy_label(result["periods"]["current_quarter"]["period_end"])} results ✓'
            for key, label in METRICS:
                m = result["metrics"].get(key)
                if m and m["current"] is not None:
                    lines.append(f'{label} ₹{m["current"]:,.0f} cr · YoY {_pct(m["yoy_pct"])} · QoQ {_pct(m["qoq_pct"])}')
        else:
            lines.append("Results filed. Numbers not verified yet.")
    elif bio_lines:
        lines.extend(bio_lines)
    else:
        lines.append(" ".join(event["headline"].split())[:180])
    return {"key": event_key(event), "kind": "filing", "symbol": event["symbol"], "category": event["category"],
            "priority": event["priority"], "time": event["time"], "title": title, "lines": lines, "pdf": event.get("pdf"),
            "verified": verified, "severity": 0.0, "move_key": None, "lane": None}


def move_card(move: dict, context: str | None) -> dict:
    kind = "Unusual move" if context else "Unexplained move"
    lines = [_move_text(move), context or "No filing since the previous close."]
    return {"key": f'move:{move["symbol"]}:{move["date"]}', "kind": "move", "symbol": move["symbol"], "category": "move",
            "priority": 1 if move["price_breach"] else 2, "time": f'{move["date"]}T15:30:00',
            "title": f'{_name(move["symbol"])} · {kind}', "lines": lines, "pdf": None, "verified": False,
            "severity": move["severity"], "price_breach": move["price_breach"], "move_key": None, "lane": None}


def build_cards(events: list[dict], moves: list[dict], results: list[dict], sent: dict, since: datetime, now: datetime,
                previous: str, bios: dict | None = None) -> list[dict]:
    """New filing cards in the window, with each price move merged in or given its own card."""
    current = [e for e in events if e.get("time") and _dt(e["time"]) <= now]
    cards = {event_key(e): filing_card(e, results, bios) for e in current
             if since <= _dt(e["time"]) and event_key(e) not in sent}
    separate = []
    for move in moves:
        key = f'move:{move["symbol"]}:{move["date"]}'
        if key in sent:
            continue
        event = linked_event(move, current, move.get("previous", previous), now)
        card = cards.get(event_key(event)) if event else None
        if card is not None:  # filing not sent yet, so one message can carry both
            card["lines"].append(_move_text(move, event["time"][:10]))
            card["severity"], card["move_key"] = move["severity"], key
            continue
        context = None
        if event:
            context = f'{LABEL.get(event["category"], event["category"])} filed {_day(event["time"])}.'
        separate.append(move_card(move, context))
    return list(cards.values()) + separate


def wants_ping(card: dict, ping_categories: set[str], headlines: dict[str, str]) -> bool:
    if card["kind"] == "move":
        return bool(card.get("price_breach"))
    if card["priority"] != 1 or card["category"] not in ping_categories:
        return False
    return card["category"] != "credit_rating" or bool(_NEGATIVE_RATING.search(headlines.get(card["key"], "")))


def score(card: dict) -> float:
    weight = 5 if card["kind"] == "move" else 3
    return BASE_SCORE.get(card["category"], 50) + min(30 if card["kind"] == "move" else 15, card["severity"] * weight)


def assign_lanes(cards: list[dict], remaining_pings: int, ping_categories: set[str], headlines: dict[str, str]) -> list[dict]:
    """Highest-scoring eligible cards ping until the day's cap; everything else goes to the digest."""
    ordered = sorted(cards, key=lambda c: (-score(c), c["time"]))
    for card in ordered:
        if wants_ping(card, ping_categories, headlines) and remaining_pings > 0:
            card["lane"] = "ping"
            remaining_pings -= 1
        else:
            card["lane"] = "digest"
    return ordered


def window_view(feed: dict, prices: dict, results: list[dict], now: datetime, hours: float, alerts: dict,
                ping_categories: set[str], bios: dict | None = None) -> dict:
    """The digest for any window the user picks: every P1/P2 card and move, with routine filings listed separately."""
    since = now - timedelta(hours=hours)
    series, market = prices.get("series", {}), prices.get("market", [])
    moves = []
    newest = latest_session(series, now)
    sessions = sorted({b["date"] for bars in series.values() for b in bars if since.date().isoformat() <= b["date"] <= (newest or "")})
    for session in sessions:
        previous = previous_session(series, session)
        moves += [{**m, "previous": previous} for m in moves_for(series, market, session, alerts)]
    cards = build_cards(feed.get("events", []), moves, results, {}, since, now, "", bios)
    headlines = {event_key(e): e["headline"] for e in feed.get("events", [])}
    for card in cards:
        card["would_ping"] = wants_ping(card, ping_categories, headlines)
        card["score"] = round(score(card), 1)
    cards.sort(key=lambda c: (c["priority"], -c["score"], c["time"]))
    routine = [{"symbol": i["symbol"], "headline": " ".join(i["headline"].split())[:140], "time": i["time"], "pdf": i.get("pdf"), "category": i["category"]}
               for i in feed.get("items", []) if i["priority"] == 3 and i.get("time") and since <= _dt(i["time"]) <= now]
    routine.sort(key=lambda r: r["time"], reverse=True)
    return {"as_of": now.isoformat(), "hours": hours, "session": newest, "cards": cards, "routine": routine, "routine_count": len(routine)}


def pings_sent_on(sent: dict, day: str) -> int:
    return sum(1 for v in sent.values() if v["lane"] == "ping" and v["at"][:10] == day)


def ping_text(card: dict) -> str:
    return "\n".join([f"<b>{html.escape(card['title'])}</b>", *(html.escape(l) for l in card["lines"])])


def digest_messages(cards: list[dict], routine: int, day: str) -> list[str]:
    """Digest text, split so no message goes over Telegram's limit."""
    header = f"<b>FINN digest · {day}</b>\n{len(cards)} to look at · routine ({routine})"
    entries = []
    for c in sorted(cards, key=lambda c: (c["priority"], -score(c))):
        body = " · ".join(html.escape(l) for l in c["lines"])
        link = f' <a href="{html.escape(c["pdf"], quote=True)}">filing</a>' if c["pdf"] else ""
        entries.append(f"• <b>{html.escape(c['title'])}</b>\n{body}{link}")
    messages, current = [], header
    for entry in entries:
        if len(current) + len(entry) + 2 > MAX_MESSAGE:
            messages.append(current)
            current = entry
        else:
            current += "\n\n" + entry
    messages.append(current)
    return messages


def _telegram(token: str, method: str, payload: dict) -> dict:
    """Errors are rewritten because the request URL contains the bot token."""
    try:
        r = requests.post(TELEGRAM.format(token=token, method=method), json=payload, timeout=30)
    except requests.RequestException as e:
        raise RuntimeError(f"Telegram request failed: {type(e).__name__}") from None
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok or not body.get("ok"):
        raise RuntimeError(f"Telegram error {r.status_code}: {str(body.get('description', ''))[:160]}")
    return body["result"]


def resolve_chat(token: str, state: dict) -> str:
    if os.environ.get("TELEGRAM_CHAT_ID"):
        return os.environ["TELEGRAM_CHAT_ID"]
    if state.get("chat_id"):
        return state["chat_id"]
    updates = _telegram(token, "getUpdates", {})
    chats = [u["message"]["chat"]["id"] for u in updates if u.get("message")]
    if not chats:
        raise SystemExit("No chat found. Send your bot any message in Telegram, then run this again.")
    state["chat_id"] = str(chats[-1])
    return state["chat_id"]


def send(token: str, chat: str, text: str, pdf: str | None = None) -> None:
    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    buttons = []
    if pdf:
        buttons.append({"text": "Open filing", "url": pdf})
    if os.environ.get("FINN_APP_URL"):
        buttons.append({"text": "Dig in", "url": os.environ["FINN_APP_URL"]})
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [buttons]}
    _telegram(token, "sendMessage", payload)


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"sent": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=1))


def _read(name: str, default: dict) -> dict:
    path = DATA / name
    return json.loads(path.read_text()) if path.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true", help="send the morning digest instead of pings")
    ap.add_argument("--dry-run", action="store_true", help="print only; send and save nothing")
    ap.add_argument("--as-of", help="replay a past moment, IST, e.g. 2026-11-05T17:00; implies --dry-run")
    ap.add_argument("--hours", type=float, help="how far back to look; default 24")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    dry = args.dry_run or bool(args.as_of)
    now = _dt(args.as_of + ":00" if args.as_of and len(args.as_of) == 16 else args.as_of) if args.as_of else datetime.now(IST).replace(tzinfo=None, microsecond=0)
    state = {"sent": {}} if args.as_of else load_state()
    feed = json.loads((DATA / "announcements.json").read_text())
    prices = _read("prices.json", {"series": {}, "market": []})
    results = _read("results.json", {"results": []})["results"]

    since = now - timedelta(hours=args.hours or 24)
    if args.digest and state.get("last_digest") and not args.hours:
        since = _dt(state["last_digest"])

    settings = _read("settings.json", {})  # chosen in the app, brought here by ingest.publish
    alerts = {**CFG["alerts"], **settings.get("alerts", {})}
    max_pings = settings.get("max_pings_per_day", CFG["push"]["max_pings_per_day"])
    moves, previous = [], ""
    session = latest_session(prices["series"], now)
    if session:
        previous = previous_session(prices["series"], session)
        moves = moves_for(prices["series"], prices.get("market", []), session, alerts)

    cards = build_cards(feed["events"], moves, results, state["sent"], since, now, previous, _read("bios.json", {}))
    headlines = {event_key(e): e["headline"] for e in feed["events"]}
    day = now.date().isoformat()
    remaining = 0 if args.digest else max(0, max_pings - pings_sent_on(state["sent"], day))
    cards = assign_lanes(cards, remaining, set(CFG["push"]["ping_categories"]), headlines)
    routine = sum(1 for i in feed["items"] if i["priority"] == 3 and i.get("time") and since <= _dt(i["time"]) <= now)
    pings = [c for c in cards if c["lane"] == "ping"]
    digest = [c for c in cards if c["lane"] == "digest"]
    print(f"as of {now} | window from {since} | session {session} | pings {len(pings)} | digest {len(digest)} | routine {routine}")

    if dry:
        for c in pings:
            print("\n[PING]\n" + ping_text(c))
        print("\n[DIGEST]" if args.digest else f"\n[held for digest: {len(digest)}]")
        if args.digest:
            print("\n---\n".join(digest_messages(digest, routine, day)))
        else:
            for c in digest:
                print(f"  - {c['title']}")
        return

    (DATA / "digest.json").write_text(json.dumps({"generated_at": now.isoformat(), "routine": routine, "cards": cards}, indent=1))
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Add it to finn/.env, or use --dry-run.")
    chat = resolve_chat(token, state)

    def mark(card: dict, lane: str) -> None:
        for key in filter(None, (card["key"], card["move_key"])):
            state["sent"][key] = {"at": now.isoformat(), "lane": lane}

    if args.digest:
        for message in digest_messages(digest, routine, day) if digest else []:  # no empty digests
            send(token, chat, message)
        for card in digest:
            mark(card, "digest")
        state["last_digest"] = now.isoformat()
        save_state(state)
    for card in pings:  # saved after each send, so a crash can't cause a repeat
        send(token, chat, ping_text(card), card["pdf"])
        mark(card, "ping")
        save_state(state)
    save_state(state)
    print("sent")


if __name__ == "__main__":
    main()
