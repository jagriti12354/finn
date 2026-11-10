"""Push the laptop's latest data to the store, and bring the app's settings back.

BSE blocks cloud servers, so the laptop fetches. Publishing means the app keeps working when the laptop is off.

Run:  python -m ingest.publish
"""
from __future__ import annotations
import json, pathlib

from api.store import get_store

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
FILES = {"feed": "announcements.json", "prices": "prices.json", "results": "results.json"}


def main():
    store = get_store()
    print("store:", type(store).__name__)
    for key, name in FILES.items():
        path = DATA / name
        if path.exists():
            store.put("pipeline", key, json.loads(path.read_text()))
            print("published", key)
    settings = store.get("settings", "user")
    if settings:
        (DATA / "settings.json").write_text(json.dumps(settings, indent=1))
        print("settings pulled")


if __name__ == "__main__":
    main()
