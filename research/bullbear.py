"""Bull vs bear, built only from evidence FINN has already checked.

Each point must cite evidence by id, and any number in it must appear in that evidence.
A point that fails either test is dropped, so the model can't add outside knowledge or invent a figure.
"""
from __future__ import annotations
import hashlib, json, pathlib, re

from research.llm import complete_json

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "bullbear"
MIN_EVIDENCE = 3
TESTED = ("delivered", "partly", "missed")

_POINT = {"type": "OBJECT", "properties": {"point": {"type": "STRING"}, "evidence": {"type": "ARRAY", "items": {"type": "STRING"}}},
          "required": ["point", "evidence"]}
SCHEMA = {"type": "OBJECT", "properties": {"bull": {"type": "ARRAY", "items": _POINT}, "bear": {"type": "ARRAY", "items": _POINT}},
          "required": ["bull", "bear"]}
PROMPT = """Write a tight bull case and bear case for an Indian listed company, using ONLY the evidence below.
Do not use anything you know about the company, its sector or the economy.

Up to 3 points each way, strongest first. Each point:
- point: at most 18 plain words. Say what the evidence means for an investor, without repeating it.
- evidence: the ids it rests on.
Only use numbers that appear in the evidence you cite. If the evidence doesn't support a side, return fewer points or none.

EVIDENCE:
{evidence}
"""


def catalogue(numbers: dict, guidance: dict) -> dict[str, dict]:
    """Everything a point may cite: rule-based signals and promise outcomes, each with its source."""
    items: list[dict] = []
    for key in ("trajectory", "balance_sheet", "cash_quality"):
        section = numbers["sections"][key]
        for s in section["signals"]:
            if s["tone"] != "neutral":
                items.append({"text": s["text"], "source": section["source"]})
    for r in guidance.get("rows", []):
        if r["verdict"] in TESTED:  # a promise not yet due proves nothing either way
            actual = f' (actual {r["actual"]["value"]})' if r.get("actual") else ""
            items.append({"text": f'Management promised: {r["promise"]}. Outcome: {r["verdict"]}{actual}.', "source": r["source"]})
    return {f"E{i}": item for i, item in enumerate(items, 1)}


def validate(points: list[dict], evidence: dict[str, dict]) -> tuple[list[dict], int]:
    kept = []
    for p in points:
        cited = [evidence[e] for e in p.get("evidence", []) if e in evidence]
        support = " ".join(c["text"] for c in cited)
        numbers = re.findall(r"\d+(?:\.\d+)?", p["point"])
        if cited and all(n in support for n in numbers):
            kept.append({"point": p["point"], "evidence": cited})
    return kept, len(points) - len(kept)


def build(numbers: dict, guidance: dict, models: list[str], key: str) -> dict:
    evidence = catalogue(numbers, guidance)
    if len(evidence) < MIN_EVIDENCE:
        return {"available": False, "reason": "Not enough checked evidence for a fair case each way.", "bull": [], "bear": []}
    prompt = PROMPT.format(evidence=json.dumps({k: v["text"] for k, v in evidence.items()}, ensure_ascii=False))
    path = CACHE / f"{hashlib.sha256(prompt.encode()).hexdigest()}.json"
    if path.exists():
        saved = json.loads(path.read_text())
    else:
        data, tokens, model = complete_json(prompt, SCHEMA, models, key, ("bull", "bear"))
        saved = {"data": data, "tokens": tokens, "model": model}
        if not model.startswith("groq/"):  # a fallback answer is fine for now but shouldn't be kept
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(saved))
    bull, dropped_bull = validate(saved["data"]["bull"], evidence)
    bear, dropped_bear = validate(saved["data"]["bear"], evidence)
    return {"available": True, "bull": bull, "bear": bear, "points_dropped": dropped_bull + dropped_bear, "tokens": saved["tokens"], "model": saved["model"]}
