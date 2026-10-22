"""Test selection and pytest output handling for `se2mod test` (reused by the pytest gates).

* `ImportGraph`: a static (ast) import graph over a mod's Python files, used to map changed source files, a module or
  a block name to the test modules that import them, transitively. Conservative: when in doubt it selects more.
* `changed_files`: files changed in the working tree and index versus HEAD (or a base ref), plus untracked files.
* `registered_markers` / `fast_expression`: the `-m` filter for the fast tier, built only from markers the mod registers.
* `parse_summary` / `failure_sections`: read pytest's own final summary line and failure blocks from captured output.
"""

from __future__ import annotations

import ast
import configparser
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["FAST_EXCLUDE", "ImportGraph", "SelectionError", "Summary", "changed_files", "failure_sections",
           "fast_expression", "is_test_file", "parse_summary", "registered_markers"]

FAST_EXCLUDE = ("build", "sdkbuild", "slow")
_SKIP_DIRS = {"Assets", "Content", "bin", "obj", "node_modules", "__pycache__", "site-packages"}
_MAX_PY_BYTES = 4_000_000


class SelectionError(Exception):
    """A problem the user can fix; reported as one sentence."""


def is_test_file(path: Path) -> bool:
    return path.suffix == ".py" and (path.name.startswith("test_") or path.stem.endswith("_test"))


def _tokens(stem: str) -> list[str]:
    return [t for t in re.split(r"[_\-.]+", stem.lower()) if t]


def _py_files(root: Path):
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if not d.startswith(".") and d not in _SKIP_DIRS)
        for fn in sorted(fns):
            if fn.endswith(".py"):
                yield Path(dp) / fn


def _module_names(path: Path, root: Path) -> list[str]:
    """Every dotted name `path` may be imported as: package-qualified, plus each shorter suffix up to `root` (covers
    namespace packages and sys.path inserts; over-matching only selects more tests)."""
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return [".".join(parts[i:]) for i in range(len(parts)) if parts[i:]]


def _package_of(path: Path) -> list[str]:
    """Dotted package of `path`, from `__init__.py` files (for resolving relative imports)."""
    pkg: list[str] = []
    d = path.parent
    while (d / "__init__.py").is_file():
        pkg.insert(0, d.name)
        d = d.parent
    return pkg


def _imports(tree: ast.AST, path: Path) -> set[str]:
    out: set[str] = set()

    def add(name: str) -> None:
        parts = name.split(".")
        out.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                pkg = _package_of(path)  # for an __init__.py this is its own package, as Python resolves it
                base = pkg[:len(pkg) - (node.level - 1)] if node.level - 1 <= len(pkg) else []
                mod = ".".join([*base, *([node.module] if node.module else [])])
            else:
                mod = node.module or ""
            if mod:
                add(mod)
            for a in node.names:
                if a.name != "*":
                    add(f"{mod}.{a.name}" if mod else a.name)
    return out


def _strings(tree: ast.AST) -> set[str]:
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and 0 < len(n.value) < 300}


@dataclass
class ImportGraph:
    root: Path
    tests_dir: Path
    files: list[Path] = field(default_factory=list)
    by_name: dict[str, set[Path]] = field(default_factory=dict)
    importers: dict[Path, set[Path]] = field(default_factory=dict)
    strings: dict[Path, set[str]] = field(default_factory=dict)
    imported: dict[Path, set[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, root: Path, tests_dir: Path) -> ImportGraph:
        g = cls(Path(root).resolve(), Path(tests_dir).resolve())
        trees: dict[Path, ast.AST] = {}
        for f in _py_files(g.root):
            g.files.append(f)
            for name in _module_names(f, g.root):
                g.by_name.setdefault(name, set()).add(f)
            try:
                if f.stat().st_size > _MAX_PY_BYTES:
                    continue
                trees[f] = ast.parse(f.read_bytes(), filename=str(f))
            except (SyntaxError, ValueError, OSError):
                continue  # an unparsable file imports nothing we can see; it can still be selected itself
        for f, tree in trees.items():
            g.strings[f] = _strings(tree)
            g.imported[f] = _imports(tree, f)
            for name in g.imported[f]:
                for target in g.by_name.get(name, ()):
                    if target != f:
                        g.importers.setdefault(target, set()).add(f)
            # importing a.b.c also runs a/__init__.py and a/b/__init__.py: covered because `add` adds every prefix
        return g

    def _is_test(self, p: Path) -> bool:
        return is_test_file(p) and (p == self.tests_dir or self.tests_dir in p.parents)

    def tests(self) -> list[Path]:
        return [f for f in self.files if self._is_test(f)]

    def _closure(self, seeds) -> set[Path]:
        seen = set(seeds)
        stack = list(seen)
        while stack:
            for imp in self.importers.get(stack.pop(), ()):
                if imp not in seen:
                    seen.add(imp)
                    stack.append(imp)
        return seen

    def _conftests_reached(self, reached: set[Path]) -> set[Path]:
        return {f for f in reached if f.name == "conftest.py"}

    def _tests_below(self, conftest: Path) -> set[Path]:
        return {t for t in self.tests() if conftest.parent in t.parents}

    def _naming(self, p: Path) -> set[Path]:
        """Files with a string constant naming `p`: its file name (or a path ending in it), its repo-relative path, or
        (for a Python file) one of its dotted module names, as `importlib.import_module` / `__import__` take them."""
        rel = p.relative_to(self.root).as_posix() if self.root in p.parents else None
        dotted = {n for n in _module_names(p, self.root) if "." in n} if p.suffix == ".py" and rel else set()
        hits = set()
        for f, strs in self.strings.items():
            if f == p:
                continue
            for s in strs:
                if s == p.name or s.endswith(("/" + p.name, "\\" + p.name)) or (rel and rel in s) or s in dotted:
                    hits.add(f)
                    break
        return hits

    def _importing(self, p: Path) -> set[Path]:
        """Files importing `p` by any of its module names; works for deleted and renamed-away paths too."""
        if p in self.importers:
            return set(self.importers[p])
        names = set(_module_names(p, self.root)) if self.root in p.parents else set()
        return {f for f, imp in self.imported.items() if f != p and imp & names}

    def tests_for(self, changed) -> list[Path]:
        """Test modules affected by `changed` paths (which may no longer exist: deleted or renamed away). Seeds: the
        file itself, files importing it by module name, and files naming it in a string constant (path, file name or
        dotted module name). The seeds' transitive importers are added; a test among them is selected, and a
        conftest.py among them (or changed itself) selects every test below it, since its fixtures run for them."""
        seeds: set[Path] = set()
        for p in (Path(os.path.abspath(c)) for c in changed):
            if p.suffix == ".py":
                seeds.add(p)
                seeds |= self._importing(p)
            seeds |= self._naming(p)
        reached = self._closure(seeds)
        tests = {f for f in reached if self._is_test(f) and f.exists()}
        for conf in self._conftests_reached(reached):
            tests |= self._tests_below(conf)
        return sorted(tests)

    def tests_for_module(self, module: str) -> list[Path]:
        path = Path(module)
        if module.endswith(".py") or os.sep in module or "/" in module:
            target = (path if path.is_absolute() else Path.cwd() / path).resolve()
            if target not in set(self.files):
                raise SelectionError(f"Module file {module} is not a Python file under {self.root}.")
            return self.tests_for([target])
        found = self.by_name.get(module)
        if not found:
            raise SelectionError(f"No module named {module} under {self.root}.")
        return self.tests_for(found)

    def tests_for_block(self, block: str) -> list[Path]:
        """Tests about a block: test files with `block` as a name token (or token run), and tests that import a module so
        named directly or through helpers in the tests dir. Unlike `tests_for`, it does not walk through other source
        modules (a block module imported by a shared module would otherwise pull in most of the suite)."""
        want = _tokens(block)
        if not want:
            return []

        def hit(p: Path) -> bool:
            toks = _tokens(p.stem)
            n = len(want)
            return any(toks[i:i + n] == want for i in range(len(toks) - n + 1))

        def in_tests(p: Path) -> bool:
            return self.tests_dir in p.parents

        seen = {f for f in self.files if hit(f) and not self._is_test(f)}
        stack = list(seen)
        while stack:
            for imp in self.importers.get(stack.pop(), ()):
                if imp not in seen and in_tests(imp):
                    seen.add(imp)
                    stack.append(imp)
        return sorted({t for t in self.tests() if hit(t)} | {f for f in seen if self._is_test(f)})


def _git(repo: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(repo), *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", check=False)
    except FileNotFoundError:
        raise SelectionError("git is not installed or not on PATH.") from None
    if r.returncode != 0:
        msg = r.stderr.strip().splitlines()
        raise SelectionError(msg[-1].rstrip(".") + "." if msg else f"git {args[0]} failed in {repo}.")
    return r.stdout


def changed_files(mod_root: Path, base: str | None = None) -> list[Path]:
    """Absolute paths under `mod_root` changed in the working tree or index versus HEAD, and versus `base` if given,
    plus untracked files that are not ignored. Deleted paths and rename sources are kept (they may not exist any more):
    their importers are affected."""
    mod_root = Path(mod_root).resolve()
    try:
        inside = subprocess.run(["git", "-C", str(mod_root), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise SelectionError("git is not installed or not on PATH.") from None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise SelectionError(f"{mod_root} is not a git repository, so --changed has nothing to compare against.")
    if base is not None:
        r = subprocess.run(["git", "-C", str(mod_root), "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
                           capture_output=True, check=False)
        if r.returncode != 0:
            raise SelectionError(f"--base {base} is not a commit in {mod_root}.")
    names: set[str] = set()
    has_head = subprocess.run(["git", "-C", str(mod_root), "rev-parse", "--verify", "--quiet", "HEAD"],
                              capture_output=True, check=False).returncode == 0
    for ref in ([("HEAD",)] if has_head else []) + ([(base,)] if base else []):
        names.update(_git(mod_root, "diff", "--name-only", "--relative", "--no-renames", *ref).splitlines())
    names.update(_git(mod_root, "ls-files", "--others", "--exclude-standard").splitlines())
    return [mod_root / n for n in sorted(filter(None, names))]


_MARKER_LINE = re.compile(r"""addinivalue_line\(\s*["']markers["']\s*,\s*[rbuf]*["']\s*([A-Za-z_]\w*)""")


def _ini_markers(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z_]\w*)\s*(?:[:(]|$)", line)
        if m:
            out.add(m.group(1))
    return out


def registered_markers(mod_root: Path, tests_dir: Path) -> set[str]:
    """Marker names the mod registers: `addinivalue_line("markers", ...)` in its conftest files, and `markers` in
    pytest.ini / pyproject.toml / tox.ini / setup.cfg at the mod root. Unreadable files are skipped."""
    found: set[str] = set()
    for conf in sorted({*Path(tests_dir).rglob("conftest.py"), *Path(mod_root).glob("conftest.py")}):
        try:
            found.update(_MARKER_LINE.findall(conf.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    root = Path(mod_root)
    for name, section in (("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read(root / name, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
        if cp.has_option(section, "markers"):
            found.update(_ini_markers(cp.get(section, "markers")))
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        markers = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])
        found.update(_ini_markers("\n".join(m for m in markers if isinstance(m, str))))
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return found


def fast_expression(registered: set[str]) -> str | None:
    """`-m` expression excluding the slow tiers the mod registers, or None when it registers none of them."""
    names = sorted(m for m in FAST_EXCLUDE if m in registered)
    return " and ".join(f"not {m}" for m in names) or None


_COUNT_WORDS = {"passed": "passed", "failed": "failed", "error": "errors", "errors": "errors", "skipped": "skipped",
                "deselected": "deselected", "xfailed": "xfailed", "xpassed": "xpassed", "warning": "warnings",
                "warnings": "warnings", "rerun": "rerun", "reruns": "rerun"}
_SUMMARY = re.compile(r"^=*\s*(?P<body>.+?) in (?P<secs>\d+(?:\.\d+)?)s(?: \([\d:.]+\))?\s*=*$")
_ORDER = ("passed", "failed", "errors", "skipped", "deselected", "xfailed", "xpassed", "rerun", "warnings")


@dataclass
class Summary:
    counts: dict[str, int]
    seconds: float
    raw: str

    def get(self, key: str) -> int:
        return self.counts.get(key, 0)

    def line(self) -> str:
        parts = [f"{self.get(k)} {k}" for k in ("passed", "failed")]
        if self.get("errors"):
            parts.append(f"{self.get('errors')} errors")
        parts.append(f"{self.get('skipped')} skipped")
        parts += [f"{self.counts[k]} {k}" for k in _ORDER[4:] if self.counts.get(k)]
        return f"{', '.join(parts)} in {self.seconds:g}s"


def parse_summary(output: str) -> Summary | None:
    """pytest's final summary line (`3 passed, 1 failed in 0.5s`, `-q` or `====` form): the last one in `output`, so a
    summary-shaped line printed by a test earlier (captured output) never wins over the run's own. None if absent."""
    for line in reversed(output.strip().splitlines()):
        m = _SUMMARY.match(line.strip())
        if not m:
            continue
        body = m.group("body").strip()
        if body == "no tests ran":
            return Summary({}, float(m.group("secs")), line.strip())
        counts: dict[str, int] = {}
        ok = True
        for part in body.split(","):
            pm = re.fullmatch(r"\s*(\d+) (\w+)\s*", part)
            if not pm or pm.group(2) not in _COUNT_WORDS:
                ok = False
                break
            key = _COUNT_WORDS[pm.group(2)]
            counts[key] = counts.get(key, 0) + int(pm.group(1))
        if ok:
            return Summary(counts, float(m.group("secs")), line.strip())
    return None


_SECTION = re.compile(r"^=+ (?P<name>[\w ]+?) =+$")


def failure_sections(output: str) -> list[str]:
    """Lines of the FAILURES and ERRORS sections and the short test summary, without the final count line."""
    out: list[str] = []
    keep = False
    for line in output.splitlines():
        m = _SECTION.match(line)
        if m:
            keep = m.group("name").strip() in ("FAILURES", "ERRORS", "short test summary info")
        elif parse_summary(line) is not None:
            keep = False
            continue
        if keep:
            out.append(line)
    return out
