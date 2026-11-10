"""Card text for a management change. Each background says where it came from."""
from __future__ import annotations
import re
from urllib.parse import urlparse

VERBS = {"appointed": "Appointed", "reappointed": "Reappointed", "resigned": "Resigned", "retired": "Retiring", "redesignated": "New role", "other": "Change"}


_TOP = re.compile(r"\b(director|ceo|cfo|coo|managing|chairman|chairperson|chief)\b", re.I)


def top_level_change(bio: dict) -> bool:
    """A director or C-suite role, read from the filing. "Reappointed" isn't news, so it doesn't count."""
    return any(c["change_type"] != "reappointed" and _TOP.search(c.get("role") or "") for c in bio.get("changes", []))


def is_incoming(change: dict) -> bool:
    return change["change_type"] in ("appointed", "redesignated")


def lines_for(bio: dict) -> list[str]:
    out = []
    for c in bio.get("changes", [])[:2]:
        when = f' from {c["effective"]}' if c.get("effective") else ""
        role = f', {c["role"]}' if c.get("role") else ""
        out.append(f'{VERBS[c["change_type"]]}: {c["person"]}{role}{when}')
        if c.get("relationship"):
            out.append(f'Related: {c["relationship"]} (from the filing)')
        if c.get("reason"):
            out.append(f'Reason given: {c["reason"]}')
        if c.get("profile"):
            out.append(f'{c["profile"]} (from the filing)')
        elif c.get("web"):
            hosts = sorted({(s.get("title") or urlparse(s["uri"]).netloc) for s in c["web"]["sources"]})
            out.append(f'{c["web"]["summary"]} (from the web: {", ".join(hosts)})')
        elif is_incoming(c):  # only claim nothing was found once a search has actually run
            out.append("No background found in the filing or on the web." if c.get("web_checked") else "Background search still to run.")
    return out
