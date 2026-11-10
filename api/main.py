"""FINN's API, read by the mobile app.

The laptop publishes filings and prices to the store; this service reads them. Research runs as a background job
because a first run on a new ticker takes minutes.

Run locally:  uvicorn api.main:app --reload
"""
from __future__ import annotations
import json, os, pathlib
from datetime import datetime, timedelta, timezone
from typing import Literal

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.store import get_store
from ingest.enrich import choose_models
from ingest.push import window_view
from ingest.screener import CompanyNotFound, HEADERS as SCREENER_HEADERS
from research import bullbear, guidance, numbers

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config/portfolio.json").read_text())
IST = timezone(timedelta(hours=5, minutes=30))
FRESH_FOR = timedelta(days=7)  # transcripts only change quarterly
JOB_TIMEOUT = timedelta(minutes=20)

app = FastAPI(title="FINN")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","), allow_methods=["*"], allow_headers=["*"])


def _now() -> datetime:
    return datetime.now(IST).replace(tzinfo=None, microsecond=0)


def _settings() -> dict:
    saved = get_store().get("settings", "user") or {}
    return {"alerts": {**{k: CFG["alerts"][k] for k in ("volume_multiple", "volume_lookback_days", "price_move_pct")}, **saved.get("alerts", {})},
            "max_pings_per_day": saved.get("max_pings_per_day", CFG["push"]["max_pings_per_day"]),
            "ping_categories": saved.get("ping_categories", CFG["push"]["ping_categories"])}


@app.get("/health")
def health():
    feed = get_store().get("pipeline", "feed")
    return {"ok": True, "data_as_of": feed.get("fetched_at") if feed else None}


@app.get("/health/sources")
def sources():
    """Can this server reach the documents research depends on? BSE blocks some cloud servers."""
    checks = {"screener": ("https://www.screener.in/company/RELIANCE/consolidated/", SCREENER_HEADERS)}
    company = None
    try:
        company = numbers.load_company("RELIANCE")
    except Exception:
        pass
    calls = company["documents"]["concalls"] if company else []
    if calls:
        checks["transcript_pdf"] = (calls[0]["url"], {**SCREENER_HEADERS, "Referer": "https://www.bseindia.com/"})
    out = {}
    for name, (url, headers) in checks.items():
        try:
            r = requests.get(url, headers=headers, timeout=30, stream=True)
            out[name] = {"status": r.status_code, "ok": r.ok, "content_type": r.headers.get("content-type")}
            r.close()
        except requests.RequestException as e:
            out[name] = {"status": None, "ok": False, "error": type(e).__name__}
    return out


@app.get("/digest")
def digest(hours: float = Query(24, gt=0, le=720)):
    store = get_store()
    feed = store.get("pipeline", "feed")
    if not feed:
        raise HTTPException(503, "No data published yet")
    settings = _settings()
    view = window_view(feed, store.get("pipeline", "prices") or {}, (store.get("pipeline", "results") or {}).get("results", []),
                       _now(), hours, settings["alerts"], set(settings["ping_categories"]), store.get("pipeline", "bios"))
    states = store.items("card_state")
    for card in view["cards"]:
        card["state"] = states.get(card["key"])
    view["cards"] = [c for c in view["cards"] if c["state"] != "dismissed"]
    view["cards"].sort(key=lambda c: c["state"] != "pinned")
    view["data_as_of"] = feed.get("fetched_at")
    return view


class CardState(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    state: Literal["dismissed", "pinned", "none"]


@app.post("/cards/state")
def set_card_state(body: CardState):
    get_store().put("card_state", body.key, None if body.state == "none" else body.state)
    return {"ok": True}


class Alerts(BaseModel):
    volume_multiple: float = Field(ge=1, le=20)
    volume_lookback_days: int = Field(ge=5, le=60)
    price_move_pct: float = Field(gt=0, le=30)


class Settings(BaseModel):
    alerts: Alerts
    max_pings_per_day: int = Field(ge=0, le=20)
    ping_categories: list[str]


@app.get("/settings")
def get_settings():
    return _settings()


@app.put("/settings")
def put_settings(body: Settings):
    get_store().put("settings", "user", body.model_dump())
    return _settings()


def _job(symbol: str) -> dict | None:
    job = get_store().get("jobs", symbol)
    if job and job["status"] == "running" and _now() - datetime.fromisoformat(job["started_at"]) > JOB_TIMEOUT:
        return {**job, "status": "failed", "error": "Timed out"}
    return job


def run_research(symbol: str) -> None:
    store = get_store()
    try:
        figures = numbers.build(symbol)
        promised = guidance.build(symbol)
        if promised.get("pending"):
            store.put("jobs", symbol, {"status": "pending", "started_at": _now().isoformat(), "error": "Model busy"})
            store.put("research", symbol, {"updated_at": _now().isoformat(), "guidance": promised, "bull_bear": None})
            return
        key = os.environ["GEMINI_API_KEY"]
        case = bullbear.build(figures, promised, choose_models(key), key)
        store.put("research", symbol, {"updated_at": _now().isoformat(), "guidance": promised, "bull_bear": case})
        store.put("jobs", symbol, {"status": "done", "started_at": _now().isoformat()})
    except Exception as e:
        store.put("jobs", symbol, {"status": "failed", "started_at": _now().isoformat(), "error": type(e).__name__})


@app.get("/research/{symbol}")
def research(symbol: str):
    """Numbers come back immediately. The transcript analysis appears here once a job has finished."""
    symbol = symbol.strip().upper()
    try:
        figures = numbers.build(symbol)
    except CompanyNotFound as e:
        raise HTTPException(404, str(e))
    except requests.RequestException:
        raise HTTPException(502, "Could not reach the fundamentals source")
    return {**figures, "analysis": get_store().get("research", symbol), "job": _job(symbol)}


@app.post("/research/{symbol}/run", status_code=202)
def run(symbol: str, background: BackgroundTasks):
    symbol = symbol.strip().upper()
    try:
        numbers.load_company(symbol)
    except CompanyNotFound as e:
        raise HTTPException(404, str(e))
    except requests.RequestException:
        raise HTTPException(502, "Could not reach the fundamentals source")
    job = _job(symbol)
    if job and job["status"] == "running":
        return {"status": "running"}
    existing = get_store().get("research", symbol)
    fallback = existing and str((existing.get("guidance") or {}).get("model", "")).startswith("groq/")
    if existing and not fallback and existing.get("bull_bear") is not None and _now() - datetime.fromisoformat(existing["updated_at"]) < FRESH_FOR:
        return {"status": "fresh"}
    get_store().put("jobs", symbol, {"status": "running", "started_at": _now().isoformat()})
    background.add_task(run_research, symbol)
    return {"status": "started"}
