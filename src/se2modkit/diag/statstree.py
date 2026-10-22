"""The engine stat tree as text: parse a *_Stats.log ("Name: value", two spaces of indent per level, written by the
game on exit, seen under Proton) or render the stats probe's nodes the same way; compare two trees.
"""
from __future__ import annotations

import re

UNIT_RE = re.compile(r"^\s*(-?[\d,]*\.?\d+(?:[eE][-+]?\d+)?)\s*([A-Za-zµ%/]*)\s*$")
BIN = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
TIME = {"s": 1.0, "ms": 1e-3, "µs": 1e-6, "us": 1e-6, "ns": 1e-9, "ks": 1e3}
METRIC = {"": 1, "k": 1e3, "M": 1e6, "G": 1e9}


def to_number(text: str | None):
    """'5.28GiB' -> bytes, '500.28ms' -> seconds, '12.5%' -> 0.125, '1,011.90KiB' -> bytes, '7' -> 7."""
    if text is None:
        return None
    m = UNIT_RE.match(text)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    u = m.group(2)
    if u in BIN:
        return v * BIN[u]
    if u in TIME:
        return v * TIME[u]
    if u == "%":
        return v / 100
    if u in METRIC:
        return v * METRIC[u]
    if u.endswith("B/s") and u[:-3] + "B" in BIN:
        return v * BIN[u[:-3] + "B"]
    return v


def parse_stats_log(text: str) -> list:
    nodes = []
    stack = []   # (indent, name)
    for line in text.splitlines():
        if not line.strip() or ":" not in line:
            continue
        indent = len(line) - len(line.lstrip(" "))
        name, _, val = line.strip().partition(":")
        # names may contain ':' (e.g. "Cable Trays: Empty"); the value is after the LAST ": "
        if ": " in line.strip():
            name, val = line.strip().rsplit(": ", 1)
        else:
            name, val = line.strip().rstrip(":"), ""
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = "/".join([s[1] for s in stack] + [name.strip()])
        stack.append((indent, name.strip()))
        val = val.strip()
        nodes.append({"path": path, "text": val or None, "number": to_number(val) if val else None})
    return nodes


def render(nodes: list, show_hidden: bool = False) -> list:
    out = []
    hidden = []
    for n in nodes:
        if not show_hidden and (n.get("hidden") or any(n["path"].startswith(h + "/") for h in hidden)):
            hidden.append(n["path"])      # a GUI-hidden stat hides its children too (the game's own view does)
            continue
        depth = n["path"].count("/")
        name = n["path"].rsplit("/", 1)[-1]
        val = n.get("text")
        if val is None and isinstance(n.get("value"), (int, float)):
            val = f"{n['value']:.6g}{n.get('unit') or ''}"
        out.append("  " * depth + (f"{name}: {val}" if val is not None else f"{name}:"))
    return out


def compare(a: list, b: list, min_rel: float = 0.1) -> list:
    def num(n):
        if n.get("number") is not None:
            return n["number"]
        v = n.get("value")
        return v if isinstance(v, (int, float)) else to_number(n.get("text"))
    ia = {n["path"]: n for n in a}
    ib = {n["path"]: n for n in b}
    rows = []
    for p in list(ia) + [p for p in ib if p not in ia]:
        na, nb = ia.get(p), ib.get(p)
        va, vb = (num(na) if na else None), (num(nb) if nb else None)
        rel = None
        if va is not None and vb is not None:
            rel = abs(vb - va) / abs(va) if va else (0.0 if vb == 0 else float("inf"))
        flag = "only in A" if nb is None else "only in B" if na is None else ("changed" if rel is not None and rel >= min_rel else "")
        rows.append({"path": p, "a": na.get("text") if na else None, "b": nb.get("text") if nb else None,
                     "a_num": va, "b_num": vb, "rel": rel, "flag": flag})
    return rows
