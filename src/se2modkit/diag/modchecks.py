"""Mod checks: the in-game probe's PASS/FAIL list (probes/modcheck.csx) plus the log greps the checklists ask for.

A mod is named `Name:PREFIX` (`parse_mods`); its checks carry the ID tag of its prefix without the underscore
(`MyMod:MM_` -> MM-0, MM-0b load sanity, MM-SUB conditional subparts, MM-DBL doubled children, MM-7a save safety,
MM-REC recipe placement, MM-LOG its log lines). A mod's product-specific checks belong in its own probes.
A check is only as strong as its detail line: PASS means the programmatic invariant held for every block present,
not that the visual item is done. Items about looks, motion and feel stay manual.
"""
from __future__ import annotations

import re

from . import logs

ORDER = {"FAIL": 0, "WARN": 1, "PASS": 2, "SKIP": 3, "INFO": 4}


def _count(entries, rx, flags=0) -> tuple:
    r = re.compile(rx, flags)
    hits = [e for e in entries if r.search(e.text)]
    return len(hits), [f"line {e.lineno}: {e.text[:160]}" for e in hits[:3]]


def parse_mods(spec: str) -> list[tuple[str, str]]:
    """`"MyMod:MM_,Other:OT_"` -> [("MyMod", "MM_"), ("Other", "OT_")]; a name without a prefix gets ""."""
    out = []
    for part in spec.split(","):
        name, _, prefix = part.strip().partition(":")
        if name.strip():
            out.append((name.strip(), prefix.strip()))
    return out


def tag(name: str, prefix: str) -> str:
    """The check-ID tag of a mod: its prefix without the underscore, else its name without spaces."""
    return prefix.rstrip("_") if prefix else name.replace(" ", "")


def log_checks(entries: list, mods: list, since_session: bool = True) -> list:
    """The checklists' log greps, over the newest log (since the last world load by default). `mods`: (name, prefix)
    pairs (`parse_mods`)."""
    start = logs.last_session_start(entries) if since_session else 0
    ents = entries[start:]
    out = []
    def add(id_, title, n, samples, fail_if_any=True, info=False):
        status = "INFO" if info else ("FAIL" if (n and fail_if_any) else ("WARN" if n else "PASS"))
        out.append({"id": id_, "title": title, "status": status,
                    "detail": f"{n} line(s)" + (": " + " | ".join(samples) if samples else ""), "source": "log"})
    n, s = _count(ents, r"should not be on server compositions")
    add("LOG-1", "no client component on a server composition", n, s)
    n, s = _count(ents, r"Definition that is linked from")
    add("LOG-2", "no binding with a missing server prefab/composite", n, s)
    for name, prefix in mods:
        words = [re.escape(name.replace(" ", ""))] + ([re.escape(prefix)] if prefix else [])
        rx = re.compile("|".join(words), re.I)
        bad = [e for e in ents if rx.search(e.full) and re.search(r"error|exception|unsatisfied|not found", e.full, re.I)]
        add(f"{tag(name, prefix)}-LOG", f"nothing wrong on {name} lines ({'|'.join(words)} + error/exception/"
            "unsatisfied/not found)", len(bad), [f"line {e.lineno}: {e.text[:160]}" for e in bad[:3]])
    return out


def merge(probe_checks: list, log_checks_: list) -> list:
    rows = [dict(c, source=c.get("source", "game")) for c in probe_checks] + list(log_checks_)
    return sorted(rows, key=lambda r: (r["id"].split("-")[0], ORDER.get(r["status"], 9), r["id"]))


def verdict(rows: list) -> str:
    if any(r["status"] == "FAIL" for r in rows):
        return "FAIL"
    if any(r["status"] == "WARN" for r in rows):
        return "WARN"
    if rows and all(r["status"] in ("SKIP", "INFO") for r in rows):
        return "SKIP"
    return "PASS"
