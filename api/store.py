"""One small key-value store: Supabase in production, a local file when Supabase isn't configured.

Supabase is called over its REST API with `requests`, so no compiled database driver is needed.
"""
from __future__ import annotations
import json, os, pathlib, threading

import requests
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


class LocalStore:
    def __init__(self, path: pathlib.Path):
        self.path, self.lock = path, threading.Lock()

    def _load(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, collection: str, key: str):
        with self.lock:
            return self._load().get(collection, {}).get(key)

    def put(self, collection: str, key: str, value) -> None:
        with self.lock:
            data = self._load()
            data.setdefault(collection, {})[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data))

    def items(self, collection: str) -> dict:
        with self.lock:
            return self._load().get(collection, {})


class SupabaseStore:
    def __init__(self, url: str, service_key: str):
        self.endpoint = f"{url.rstrip('/')}/rest/v1/kv"
        self.headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}", "Content-Type": "application/json"}

    def get(self, collection: str, key: str):
        r = requests.get(self.endpoint, headers=self.headers, timeout=30,
                         params={"collection": f"eq.{collection}", "key": f"eq.{key}", "select": "value"})
        r.raise_for_status()
        rows = r.json()
        return rows[0]["value"] if rows else None

    def put(self, collection: str, key: str, value) -> None:
        r = requests.post(self.endpoint, headers={**self.headers, "Prefer": "resolution=merge-duplicates"}, timeout=60,
                          params={"on_conflict": "collection,key"}, json={"collection": collection, "key": key, "value": value})
        r.raise_for_status()

    def items(self, collection: str) -> dict:
        r = requests.get(self.endpoint, headers=self.headers, timeout=30, params={"collection": f"eq.{collection}", "select": "key,value"})
        r.raise_for_status()
        return {row["key"]: row["value"] for row in r.json()}


_local: dict[str, LocalStore] = {}


def get_store():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if url and key:
        return SupabaseStore(url, key)
    path = os.environ.get("FINN_STORE_PATH", str(ROOT / "data" / "store.json"))
    return _local.setdefault(path, LocalStore(pathlib.Path(path)))
