import pytest
from fastapi.testclient import TestClient

from api import main
from api.store import SupabaseStore, get_store
from research import bullbear, llm
from ingest.screener import CompanyNotFound

FIGURES = {"symbol": "X", "name": "X Ltd", "basis": "consolidated", "is_financial": False, "documents": {}, "sections": {
    k: {"source": "S", "signals": [{"tone": "positive", "text": f"{k} up 10%"}]} for k in ("trajectory", "balance_sheet", "cash_quality")}}


def _bars(last_close=100.0):
    days = [f"2026-10-{d:02d}" for d in range(1, 31)] + ["2026-11-04"]
    return [{"date": d, "close": 100.0, "volume": 1000} for d in days] + [{"date": "2026-11-05", "close": last_close, "volume": 5000}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv("FINN_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setattr(main, "_now", lambda: main.datetime(2026, 11, 5, 17, 0))
    store = get_store()
    store.put("pipeline", "feed", {"fetched_at": "2026-11-05T16:00:00",
        "events": [{"symbol": "PIIND", "category": "results", "priority": 1, "time": "2026-11-04T19:38:00", "headline": "Results", "pdf": "https://x/a.pdf", "filing_ids": [1]},
                   {"symbol": "TCS", "category": "dividend", "priority": 2, "time": "2026-10-10T10:00:00", "headline": "Dividend", "pdf": None, "filing_ids": [2]}],
        "items": [{"symbol": "ITC", "priority": 3, "time": "2026-11-05T09:00:00", "headline": "Newspaper ad", "pdf": None, "category": "unclassified"}]})
    store.put("pipeline", "prices", {"series": {"PIIND": _bars(80.0)}, "market": _bars(100.0)})
    store.put("pipeline", "results", {"results": []})
    monkeypatch.setattr(main.numbers, "build", lambda s: FIGURES)
    monkeypatch.setattr(main.numbers, "load_company", lambda s: {})
    return TestClient(main.app)


def test_health_reports_data_age(client):
    assert client.get("/health").json() == {"ok": True, "data_as_of": "2026-11-05T16:00:00"}


def test_digest_defaults_to_24_hours_and_merges_the_move(client):
    d = client.get("/digest").json()
    assert [c["symbol"] for c in d["cards"]] == ["PIIND"] and d["routine_count"] == 1
    assert any("-20.0%" in l for l in d["cards"][0]["lines"]) and d["cards"][0]["would_ping"] is True


def test_digest_window_is_user_selectable(client):
    assert {c["symbol"] for c in client.get("/digest", params={"hours": 24}).json()["cards"]} == {"PIIND"}
    assert {c["symbol"] for c in client.get("/digest", params={"hours": 720}).json()["cards"]} == {"PIIND", "TCS"}
    assert client.get("/digest", params={"hours": 0}).status_code == 422


def test_dismiss_hides_and_pin_floats(client):
    key = client.get("/digest", params={"hours": 720}).json()["cards"][-1]["key"]
    client.post("/cards/state", json={"key": key, "state": "pinned"})
    assert client.get("/digest", params={"hours": 720}).json()["cards"][0]["key"] == key
    client.post("/cards/state", json={"key": key, "state": "dismissed"})
    assert key not in [c["key"] for c in client.get("/digest", params={"hours": 720}).json()["cards"]]
    client.post("/cards/state", json={"key": key, "state": "none"})
    assert key in [c["key"] for c in client.get("/digest", params={"hours": 720}).json()["cards"]]
    assert client.post("/cards/state", json={"key": key, "state": "nonsense"}).status_code == 422


def test_thresholds_change_what_counts_as_unusual(client):
    s = client.get("/settings").json()
    assert s["alerts"]["price_move_pct"] == 4.0
    s["alerts"].update(price_move_pct=25.0, volume_multiple=20.0)
    assert client.put("/settings", json=s).status_code == 200
    d = client.get("/digest").json()
    assert not any("-20.0%" in l for l in d["cards"][0]["lines"])
    s["alerts"]["price_move_pct"] = 0
    assert client.put("/settings", json=s).status_code == 422


def test_research_returns_numbers_before_any_analysis(client):
    r = client.get("/research/x").json()
    assert r["name"] == "X Ltd" and r["analysis"] is None and r["job"] is None


def test_unknown_ticker_is_404(client, monkeypatch):
    def nope(s):
        raise CompanyNotFound("Unknown ticker")
    monkeypatch.setattr(main.numbers, "build", nope)
    monkeypatch.setattr(main.numbers, "load_company", nope)
    assert client.get("/research/NOPE").status_code == 404 and client.post("/research/NOPE/run").status_code == 404


def test_run_stores_analysis_and_a_second_run_is_free(client, monkeypatch):
    calls = []
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(main.guidance, "build", lambda s: calls.append(s) or {"rows": []})
    monkeypatch.setattr(main, "choose_models", lambda k: ["m"])
    monkeypatch.setattr(main.bullbear, "build", lambda *a: {"available": True, "bull": [], "bear": []})
    assert client.post("/research/x/run").json()["status"] == "started"
    assert client.get("/research/x").json()["job"]["status"] == "done"
    assert client.post("/research/x/run").json()["status"] == "fresh" and calls == ["X"]


def test_busy_model_leaves_job_pending_and_retryable(client, monkeypatch):
    monkeypatch.setattr(main.guidance, "build", lambda s: {"pending": True})
    client.post("/research/x/run")
    assert client.get("/research/x").json()["job"]["status"] == "pending"
    assert client.post("/research/x/run").json()["status"] == "started"


def test_a_groq_fallback_result_can_be_rerun(client, monkeypatch):
    monkeypatch.setattr(main, "run_research", lambda s: None)
    get_store().put("research", "X", {"updated_at": "2026-11-05T16:00:00", "guidance": {"model": "groq/m"}, "bull_bear": {"bull": []}})
    assert client.post("/research/x/run").json()["status"] == "started"


def test_promises_not_yet_due_are_not_evidence():
    rows = [{"verdict": "too_early", "promise": "Grow 10%", "source": "c"}, {"verdict": "missed", "promise": "Grow 20%", "source": "d", "actual": {"value": 5}}]
    evidence = bullbear.catalogue({"sections": {k: {"source": "S", "signals": []} for k in ("trajectory", "balance_sheet", "cash_quality")}}, {"rows": rows})
    assert [e["source"] for e in evidence.values()] == ["d"] and "actual 5" in evidence["E1"]["text"]


def test_a_stuck_job_stops_counting_as_running(client):
    get_store().put("jobs", "X", {"status": "running", "started_at": "2026-11-05T10:00:00"})
    assert client.get("/research/x").json()["job"]["status"] == "failed"


def test_supabase_store_upserts_by_collection_and_key(monkeypatch):
    seen = {}
    class R:
        def raise_for_status(self): pass
    def post(url, **kw):
        seen.update(url=url, **kw)
        return R()
    monkeypatch.setattr("api.store.requests.post", post)
    SupabaseStore("https://p.supabase.co/", "svc").put("c", "k", {"a": 1})
    assert seen["url"] == "https://p.supabase.co/rest/v1/kv" and seen["params"] == {"on_conflict": "collection,key"}
    assert seen["headers"]["Prefer"] == "resolution=merge-duplicates" and seen["json"] == {"collection": "c", "key": "k", "value": {"a": 1}}


EVIDENCE = {"E1": {"text": "Revenue up 12% a year.", "source": "S"}, "E2": {"text": "Margin compressing.", "source": "S"}}


def test_bull_bear_drops_uncited_points_and_outside_numbers():
    points = [{"point": "Revenue growing 12% a year", "evidence": ["E1"]}, {"point": "Market will grow 30%", "evidence": ["E1"]},
              {"point": "Brand is strong", "evidence": []}, {"point": "Cites nothing real", "evidence": ["E9"]}, {"point": "Margin pressure is the risk", "evidence": ["E2"]}]
    kept, dropped = bullbear.validate(points, EVIDENCE)
    assert [p["point"] for p in kept] == ["Revenue growing 12% a year", "Margin pressure is the risk"] and dropped == 3
    assert kept[0]["evidence"] == [EVIDENCE["E1"]]


def test_bull_bear_refuses_thin_evidence():
    thin = {"sections": {k: {"source": "S", "signals": []} for k in ("trajectory", "balance_sheet", "cash_quality")}}
    assert bullbear.build(thin, {"rows": []}, [], "k")["available"] is False


def test_groq_only_catches_short_prompts_and_checks_the_shape(monkeypatch):
    def gemini_down(*a, **k):
        raise RuntimeError("busy")
    class R:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": '{"rows": [], "summary": {}}'}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
    monkeypatch.setattr(llm, "_generate", gemini_down)
    monkeypatch.setattr(llm, "choose_groq_model", lambda k: "m")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: R())
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert llm.complete_json("short", {}, ["x"], "k", ("rows", "summary"))[2] == "groq/m"
    with pytest.raises(ValueError):
        llm.complete_json("short", {}, ["x"], "k", ("rows", "missing"))
    with pytest.raises(RuntimeError):
        llm.complete_json("x" * (llm.GROQ_MAX_PROMPT_CHARS + 1), {}, ["x"], "k", ("rows",))
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(RuntimeError):
        llm.complete_json("short", {}, ["x"], "k", ("rows",))
