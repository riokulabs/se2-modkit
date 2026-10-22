"""An ast outline of a Python file (top-level functions, classes with their methods, constants, each with line ranges)
and the numbered source of one symbol, so a reader can fetch only the lines it needs."""

from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Entry", "OutlineError", "outline", "render", "symbol_source"]

_SIG_MAX = 100


class OutlineError(Exception):
    """A problem the user can fix; reported as one sentence."""


@dataclass
class Entry:
    kind: str  # "def", "async def", "class", "name"
    name: str
    start: int
    end: int
    text: str
    children: list[Entry] = field(default_factory=list)


def _span(node: ast.AST) -> tuple[int, int]:
    start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", ()))])
    return start, node.end_lineno or node.lineno


def _deco(node) -> str:
    out = ""
    for d in node.decorator_list:
        s = ast.unparse(d)
        out += "@" + (s if len(s) <= 40 else s[:37] + "...") + " "
    return out


def _short(s: str) -> str:
    return s if len(s) <= _SIG_MAX else s[:_SIG_MAX - 3] + "..."


def _targets(node) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: list[str] = []
    for t in targets:
        if isinstance(t, ast.Name):
            names.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)) and all(isinstance(e, ast.Name) for e in t.elts):
            names.append(", ".join(e.id for e in t.elts))
    return names


def _entries(body) -> list[Entry]:
    out: list[Entry] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            a, b = _span(node)
            ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            out.append(Entry(kind, node.name, a, b, _short(f"{_deco(node)}{kind} {node.name}({ast.unparse(node.args)})"
                                                           f"{ret}")))
        elif isinstance(node, ast.ClassDef):
            a, b = _span(node)
            bases = [ast.unparse(x) for x in (*node.bases, *node.keywords)]
            text = _short(f"{_deco(node)}class {node.name}" + (f"({', '.join(bases)})" if bases else ""))
            out.append(Entry("class", node.name, a, b, text, _entries(node.body)))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name in _targets(node):
                out.append(Entry("name", name, node.lineno, node.end_lineno or node.lineno, name))
        elif isinstance(node, (ast.If, ast.Try, ast.With)) or type(node).__name__ == "TryStar":
            # defs under `if TYPE_CHECKING:` / `try: ... except ImportError:` are still top-level names
            for sub in ("body", "orelse", "finalbody"):
                out.extend(_entries(getattr(node, sub, []) or []))
            for h in getattr(node, "handlers", []) or []:
                out.extend(_entries(h.body))
    return out


def _parse(path: Path) -> tuple[list[str], list[Entry]]:
    path = Path(path)
    if not path.is_file():
        raise OutlineError(f"{path} does not exist or is not a file.")
    if path.suffix not in (".py", ".pyi"):
        raise OutlineError(f"{path} is not a Python file; outline reads .py files only (use grep -n for others).")
    try:
        data = path.read_bytes()
        tree = ast.parse(data, filename=str(path))
    except SyntaxError as e:
        raise OutlineError(f"Cannot parse {path}: {e.msg} at line {e.lineno}.") from None
    except (ValueError, OSError) as e:
        raise OutlineError(f"Cannot read {path}: {e}.") from None
    return data.decode("utf-8", "replace").splitlines(), _entries(tree.body)


def outline(path: Path) -> tuple[int, list[Entry]]:
    lines, entries = _parse(path)
    return len(lines), entries


def _rng(e: Entry) -> str:
    return f"L{e.start}" if e.start == e.end else f"L{e.start}-{e.end}"


def render(path: Path, max_lines: int = 400) -> list[str]:
    n, entries = outline(path)
    out = [f"{path} ({n} lines)"]

    def flush(run: list[Entry], pad: str) -> None:
        # a run of module/class-level names shares lines (each line keeps its own range); --symbol NAME finds one
        chunk: list[Entry] = []
        for e in run + [None]:
            if e is None or (chunk and len(", ".join(c.name for c in (*chunk, e))) > _SIG_MAX):
                if chunk:
                    a, b = chunk[0].start, max(c.end for c in chunk)
                    out.append(f"{pad}{'L%d' % a if a == b else 'L%d-%d' % (a, b)} {', '.join(c.name for c in chunk)}")
                chunk = []
            if e is not None:
                chunk.append(e)

    def walk(es: list[Entry], depth: int) -> None:
        pad = "  " * depth
        run: list[Entry] = []
        for e in es:
            if e.kind == "name":
                run.append(e)
                continue
            flush(run, pad)
            run = []
            out.append(f"{pad}{_rng(e)} {e.text}")
            walk(e.children, depth + 1)
        flush(run, pad)

    walk(entries, 0)
    if len(out) > max_lines + 1:
        rest = len(out) - 1 - max_lines
        out = out[:max_lines + 1] + [f"... {rest} more entries (raise --max-lines)"]
    return out


def _find(entries: list[Entry], parts: list[str]) -> list[Entry]:
    hits = [e for e in entries if e.name == parts[0]]
    if len(parts) == 1:
        return hits
    return [h for e in hits for h in _find(e.children, parts[1:])]


def _all_names(entries: list[Entry], prefix: str = "") -> list[str]:
    out = []
    for e in entries:
        full = prefix + e.name
        out.append(full)
        out += _all_names(e.children, full + ".")
    return out


def symbol_source(path: Path, symbol: str, max_lines: int = 200) -> list[str]:
    lines, entries = _parse(path)
    hits = _find(entries, symbol.split("."))
    if not hits:
        names = _all_names(entries)
        close = difflib.get_close_matches(symbol, names, n=3) or difflib.get_close_matches(
            symbol.split(".")[-1], [x.split(".")[-1] for x in names], n=3)
        hint = f"; did you mean {', '.join(close)}?" if close else "; run outline without --symbol to list names."
        raise OutlineError(f"No symbol {symbol} in {path}{hint}")
    e = hits[0]
    width = len(str(e.end))
    body = [f"{n:>{width}}  {lines[n - 1]}" for n in range(e.start, min(e.end, len(lines)) + 1)]
    others = f"; also defined at {', '.join(_rng(h) for h in hits[1:])}" if len(hits) > 1 else ""
    out = [f"{path} {_rng(e)} {e.text}{others}"]
    if len(body) > max_lines:
        rest = len(body) - max_lines
        cut = e.start + max_lines
        out += body[:max_lines] + [f"... {rest} more lines (sed -n {cut},{e.end}p {path})"]
    else:
        out += body
    return out
