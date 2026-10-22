"""Human-readable output helpers (plain text tables) and JSON writing."""
from __future__ import annotations

import json
import sys


def fmt_bytes(n) -> str:
    if n is None:
        return "-"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return str(n)


def fmt_ms(v) -> str:
    if v is None:
        return "-"
    v = float(v)
    if abs(v) >= 10000:
        return f"{v / 1000:.1f} s"
    if abs(v) >= 1000:
        return f"{v / 1000:.2f} s"
    return f"{v:.1f} ms" if abs(v) >= 10 else f"{v:.2f} ms"


def fmt_dur(s) -> str:
    if s is None:
        return "-"
    s = float(s)
    d, rem = divmod(int(s), 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{sec:02d}"


def fmt_num(v, digits=2) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.{digits}f}"
    return str(v)


def table(rows: list, cols: list, indent: str = "  ") -> list:
    """cols: [(header, key_or_callable)]. Returns lines."""
    if not rows:
        return [indent + "(none)"]
    cells = []
    for r in rows:
        line = []
        for _, k in cols:
            v = k(r) if callable(k) else r.get(k)
            line.append("-" if v is None else (fmt_num(v) if not isinstance(v, str) else v))
        cells.append(line)
    widths = [max(len(h), *(len(c[i]) for c in cells)) for i, (h, _) in enumerate(cols)]
    widths = [min(w, 90) for w in widths]
    def fmt(vals):
        return indent + "  ".join((v if len(v) <= w else v[: w - 1] + "…").ljust(w) for v, w in zip(vals, widths)).rstrip()
    return [fmt([h for h, _ in cols]), fmt(["-" * w for w in widths])] + [fmt(c) for c in cells]


def section(title: str) -> list:
    return ["", f"== {title}"]


def kv(pairs: list, indent: str = "  ") -> list:
    w = max((len(k) for k, _ in pairs), default=0)
    return [f"{indent}{k.ljust(w)}  {v}" for k, v in pairs]


def emit(obj, as_json: bool, lines: list, out_file: str | None = None, stream=None):
    stream = stream or sys.stdout
    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, default=str)
    if as_json:
        json.dump(obj, stream, indent=2, default=str)
        stream.write("\n")
    else:
        stream.write("\n".join(lines).lstrip("\n") + "\n")
