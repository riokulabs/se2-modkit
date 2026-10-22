"""Probe packaging (prelude + includes + parameters) and result extraction.

A probe is probes/<name>.csx: C# statements run as the body of the bridge plugin's script method. Packaging:
  1. `//#include _file.csx` lines are replaced by that file (only files named `_*.csx` in probes/, one level deep);
  2. probes/_prelude.csx is put in front;
  3. the `/*@@PARAMS@@*/` marker in the prelude becomes `__params["key"] = "value";` lines (C# string literals,
     escaped here, so a parameter can never break out of its literal);
The probe answers with one console line `@@SE2DIAG@@{json}` (Emit in the prelude).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import locations as paths

MARKER = "@@SE2DIAG@@"
PARAMS_MARKER = "/*@@PARAMS@@*/"
INCLUDE_RE = re.compile(r"^[ \t]*//#include[ \t]+(\S+)[ \t]*$", re.M)
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ProbeError(RuntimeError):
    pass


def cs_string(value: str) -> str:
    """A C# regular string literal holding exactly `value`."""
    out = ['"']
    for ch in value:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif o < 0x20 or o in (0x7F, 0x85, 0x2028, 0x2029):
            out.append(f"\\u{o:04x}")
        elif o > 0xFFFF:
            # surrogate pair as two \u escapes
            o -= 0x10000
            out.append(f"\\u{0xD800 + (o >> 10):04x}\\u{0xDC00 + (o & 0x3FF):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def list_probes(probes_dir: Path | None = None) -> list:
    d = probes_dir or paths.PROBES_DIR
    return sorted(p.stem for p in d.glob("*.csx") if not p.name.startswith("_"))


def _read(probes_dir: Path, name: str) -> str:
    p = (probes_dir / name)
    if p.parent.resolve() != probes_dir.resolve() or not p.is_file():
        raise ProbeError(f"probe file not found: {name}")
    return p.read_text(encoding="utf-8")


def expand_includes(text: str, probes_dir: Path) -> str:
    def repl(m):
        inc = m.group(1)
        if not re.fullmatch(r"_[a-z0-9_]+\.csx", inc):
            raise ProbeError(f"bad include name {inc!r} (only _name.csx files in probes/)")
        body = _read(probes_dir, inc)
        if INCLUDE_RE.search(body):
            raise ProbeError(f"nested include in {inc} (one level only)")
        return f"// ---- begin {inc} ----\n{body.rstrip()}\n// ---- end {inc} ----"
    return INCLUDE_RE.sub(repl, text)


def build(name: str, params: dict | None = None, probes_dir: Path | None = None) -> str:
    d = probes_dir or paths.PROBES_DIR
    if not NAME_RE.match(name):
        raise ProbeError(f"bad probe name {name!r}")
    prelude = _read(d, "_prelude.csx")
    if PARAMS_MARKER not in prelude:
        raise ProbeError("prelude lacks the parameter marker")
    body = expand_includes(_read(d, f"{name}.csx"), d)
    all_params = {"__probe": name}
    for k, v in (params or {}).items():
        if not KEY_RE.match(k):
            raise ProbeError(f"bad parameter name {k!r}")
        if isinstance(v, bool):
            v = "true" if v else "false"
        all_params[k] = str(v)
    lines = "\n".join(f"__params[{cs_string(k)}] = {cs_string(v)};" for k, v in all_params.items())
    return prelude.replace(PARAMS_MARKER, lines, 1) + f"\n// ==== probe {name} ====\n" + body


@dataclass
class ProbeResult:
    name: str
    data: object = None
    missing: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    elapsed_ms: float = 0.0
    console: list = field(default_factory=list)   # other console lines

    def to_json(self) -> dict:
        return {"probe": self.name, "elapsed_ms": self.elapsed_ms, "missing": self.missing, "notes": self.notes,
                "data": self.data}


COMPILE_ERR_RE = re.compile(r"\berror CS\d{4}\b")


def parse_output(name: str, text: str, is_error: bool = False) -> ProbeResult:
    """Find the last marker line. Anything else is kept as console notes. Raise ProbeError on failure."""
    payload = None
    console = []
    for line in text.splitlines():
        idx = line.find(MARKER)
        if idx >= 0:
            payload = line[idx + len(MARKER):]
        elif line.strip():
            console.append(line)
    if payload is None:
        head = "\n".join(console[:40])
        if COMPILE_ERR_RE.search(text):
            raise ProbeError(f"probe {name} did not compile in this game build:\n{head}")
        raise ProbeError(f"probe {name} produced no result{' (script error)' if is_error else ''}:\n{head}")
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as e:
        raise ProbeError(f"probe {name} result is not valid JSON ({e})") from None
    return ProbeResult(name=name, data=obj.get("data"), missing=obj.get("missing") or [],
                       notes=obj.get("notes") or [], elapsed_ms=obj.get("elapsed_ms") or 0.0, console=console)


def run(client, name: str, params: dict | None = None, target: str = "main") -> ProbeResult:
    code = build(name, params)
    res = client.execute(code, target=target)
    return parse_output(name, "\n".join(res.texts), res.is_error)
