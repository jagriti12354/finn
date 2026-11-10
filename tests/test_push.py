from datetime import datetime

import pytest
import requests

from ingest import push
from ingest.push import (assign_lanes, build_cards, digest_messages, fy_label, latest_session, moves_for, pings_sent_on,
                         ping_text, previous_session, wants_ping)

ALERTS = {"volume_multiple": 2.0, "volume_lookback_days": 20, "price_move_pct": 4.0}
PING = {"results", "management_change", "M&A", "pledging", "credit_rating"}
NOW = datetime(2026, 11, 5, 17, 0)
SINCE = datetime(2026, 11, 4, 17, 0)


def bars(last_close=100.0, last_volume=1_000_000, last_date="2026-11-05"):
    days = [f"2026-10-{d:02d}" for d in range(1, 31)] + ["2026-11-04"]
    out = [{"date": d, "close": 100.0, "volume": 1_000_000} for d in days]
    return out + [{"date": last_date, "close": last_close, "volume": last_volume}]


def event(symbol="PIIND", category="results", priority=1, time="2026-11-04T19:38:00", headline="Unaudited Financial Results", ids=(1,)):
    return {"symbol": symbol, "category": category, "priority": priority, "time": time, "headline": headline,
            "pdf": "https://x/a.pdf", "filing_ids": list(ids)}


def move(symbol="PIIND", change=-19.1, volume=30.1, relative=None, date="2026-11-05", market=0.2):
    rel = change - market if relative is None else relative
    return {"symbol": symbol, "date": date, "close": 90.0, "change_pct": change, "volume_x": volume, "rules": ["price"],
            "market_change_pct": market, "relative_pct": rel, "price_breach": abs(rel) >= 4, "severity": max(abs(rel) / 4, volume / 2)}


def cards(events, moves=(), sent=None, results=()):
    return build_cards(events, list(moves), list(results), sent or {}, SINCE, NOW, "2026-11-04")


def test_fy_label():
    assert fy_label("2026-09-30") == "Q2 FY27" and fy_label("2027-03-31") == "Q4 FY27" and fy_label("2026-06-30") == "Q1 FY27"


def test_partial_bar_is_not_used_before_the_close():
    series = {"A": bars(last_date="2026-11-05")}
    assert latest_session(series, datetime(2026, 11, 5, 11, 0)) == "2026-11-04"
    assert latest_session(series, datetime(2026, 11, 5, 16, 30)) == "2026-11-05"
    assert previous_session(series, "2026-11-05") == "2026-11-04"


def test_price_and_volume_breach_are_one_move():
    m = moves_for({"A": bars(105.0, 3_000_000)}, [], "2026-11-05", ALERTS)
    assert len(m) == 1 and sorted(m[0]["rules"]) == ["price", "volume"]


def test_market_wide_move_is_not_a_price_breach():
    index = bars(105.0)
    m = moves_for({"A": bars(105.0)}, index, "2026-11-05", ALERTS)
    assert m[0]["market_change_pct"] == 5.0 and m[0]["relative_pct"] == 0.0 and m[0]["price_breach"] is False


def test_move_merges_into_an_unsent_filing():
    out = cards([event()], [move()])
    assert len(out) == 1 and out[0]["move_key"] == "move:PIIND:2026-11-05"
    assert any("Next session (5 Nov): -19.1% on 30.1x volume" in l for l in out[0]["lines"])


def test_move_after_filing_was_sent_gets_its_own_card_with_timing_only():
    out = cards([event()], [move()], sent={"PIIND:results:1": {"at": "x", "lane": "ping"}})
    assert len(out) == 1 and out[0]["kind"] == "move" and "Results filed 4 Nov." in out[0]["lines"]
    assert "because" not in ping_text(out[0]).lower()


def test_filing_after_the_close_is_not_linked_to_that_days_move():
    # Dixon, 30 Oct: fell during the session, filed results at 16:19
    late = event(symbol="DIXON", time="2026-11-05T16:19:00")
    out = cards([late], [move("DIXON", -4.2, 2.0)])
    filing = next(c for c in out if c["kind"] == "filing")
    assert filing["move_key"] is None and any("Unexplained" in c["title"] for c in out)


def test_flat_growth_never_shows_negative_zero():
    assert push._pct(-0.0) == "+0.0%" and push._pct(-0.04) == "+0.0%" and push._pct(None) == "n.m."


def test_move_with_no_filing_is_unexplained():
    out = cards([], [move("ANGELONE", 7.0, 2.9)])
    assert "Unexplained move" in out[0]["title"]


def test_already_sent_items_are_not_repeated():
    sent = {"PIIND:results:1": {"at": "x", "lane": "ping"}, "move:PIIND:2026-11-05": {"at": "x", "lane": "ping"}}
    assert cards([event()], [move()], sent=sent) == []


def test_verified_numbers_show_and_unverified_do_not():
    m = {"current": 1701.8, "yoy_pct": -9.1, "qoq_pct": -4.1}
    result = {"symbol": "PIIND", "event_time": "2026-11-04T19:44:00", "status": "verified", "metrics": {"revenue": m},
              "periods": {"current_quarter": {"period_end": "2026-09-30"}}}
    card = cards([event()], results=[result])[0]
    assert "Q2 FY27 results ✓" in card["title"] and "Revenue ₹1,702 cr · YoY -9.1% · QoQ -4.1%" in card["lines"]
    bad = cards([event()], results=[{**result, "status": "mismatch"}])[0]
    assert "✓" not in bad["title"] and bad["lines"] == ["Results filed. Numbers not verified yet."]


@pytest.mark.parametrize("category,priority,headline,expected", [
    ("results", 1, "x", True), ("M&A", 1, "x", True), ("fund_raise", 1, "x", False), ("dividend", 2, "x", False),
    ("credit_rating", 1, "Reaffirmed by ICRA", False), ("credit_rating", 1, "Rating downgraded by CRISIL", True)])
def test_who_gets_to_ping(category, priority, headline, expected):
    e = event(category=category, priority=priority, headline=headline)
    card = cards([e])[0]
    assert wants_ping(card, PING, {card["key"]: headline}) is expected


def test_cap_keeps_the_biggest_and_sends_the_rest_to_digest():
    many = [event(symbol=f"S{i}", ids=(i,)) for i in range(7)]
    out = assign_lanes(cards(many), 5, PING, {})
    assert sum(c["lane"] == "ping" for c in out) == 5 and sum(c["lane"] == "digest" for c in out) == 2


def test_big_unexplained_move_can_outrank_a_filing():
    out = assign_lanes(cards([event(symbol="A", ids=(1,))], [move("B", -19.0, 30.0)]), 1, PING, {})
    assert next(c for c in out if c["symbol"] == "B")["lane"] == "ping"


def test_pings_count_only_for_that_day():
    sent = {"a": {"lane": "ping", "at": "2026-11-05T10:00:00"}, "b": {"lane": "ping", "at": "2026-11-04T10:00:00"},
            "c": {"lane": "digest", "at": "2026-11-05T08:30:00"}}
    assert pings_sent_on(sent, "2026-11-05") == 1


def test_digest_escapes_and_splits():
    long = [{**cards([event(symbol=f"S{i}", category="dividend", priority=2, headline="A & B <x> " * 15, ids=(i,))])[0], "lane": "digest"} for i in range(60)]
    msgs = digest_messages(long, 7, "2026-11-05")
    assert len(msgs) > 1 and all(len(m) <= 3600 for m in msgs) and "routine (7)" in msgs[0]
    assert "A &amp; B &lt;x&gt;" in msgs[0]


def test_telegram_errors_never_contain_the_token(monkeypatch):
    def boom(url, **kw):
        raise requests.ConnectionError(f"failed for {url}")
    monkeypatch.setattr("ingest.push.requests.post", boom)
    with pytest.raises(RuntimeError) as e:
        push._telegram("SECRET-TOKEN", "sendMessage", {})
    assert "SECRET-TOKEN" not in str(e.value)
