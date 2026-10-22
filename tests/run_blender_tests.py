"""Run the `blender`-marked tests inside Blender: `se2mod blender run tests/run_blender_tests.py [-- PYTEST ARGS]`.

Blender's own Python has no pytest, so this puts the pytest packages of a regular interpreter on sys.path (only those
packages, never its whole site-packages: its numpy would shadow Blender's). The interpreter is `$SE2MODKIT_PYTHON`, else
`python3` on PATH. Default arguments: `-m blender tests` (from the repository root); exits with pytest's status.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("pytest", "_pytest", "pluggy", "iniconfig", "packaging", "pygments", "py")
_FIND = ("import importlib.util, os\n"
         "for n in {names!r}:\n"
         "    s = importlib.util.find_spec(n)\n"
         "    print(os.path.dirname(s.origin) if s and s.origin and s.origin.endswith('__init__.py') else s.origin "
         "if s else '')\n")


def _pytest_dirs() -> list[Path]:
    exe = os.environ.get("SE2MODKIT_PYTHON") or shutil.which("python3")
    if not exe:
        raise SystemExit("No Python with pytest found: set SE2MODKIT_PYTHON to an interpreter that has pytest.")
    out = subprocess.run([exe, "-I", "-c", _FIND.format(names=PACKAGES)], capture_output=True, text=True, check=False)
    paths = [Path(p) for p in out.stdout.splitlines()]
    missing = [n for n, p in zip(PACKAGES, paths) if not str(p)]
    if out.returncode or len(paths) != len(PACKAGES) or "pytest" in missing:
        raise SystemExit(f"{exe} has no pytest: set SE2MODKIT_PYTHON to an interpreter that has pytest.")
    return [p for p in paths if str(p)]


def _shim(dirs: list[Path]) -> Path:
    """A temp folder holding links to just these packages (or module files)."""
    shim = Path(tempfile.mkdtemp(prefix="se2modkit-pytest-"))
    for d in dirs:
        try:
            (shim / d.name).symlink_to(d, target_is_directory=d.is_dir())
        except OSError:
            (shutil.copytree if d.is_dir() else shutil.copy2)(d, shim / d.name)
    return shim


def main(argv: list[str]) -> int:
    shim = _shim(_pytest_dirs())
    try:
        sys.path.insert(0, str(shim))
        sys.path.insert(0, str(ROOT / "src"))
        import pytest
        os.chdir(ROOT)
        return int(pytest.main(argv or ["-m", "blender", "-p", "no:cacheprovider", "tests"]))
    finally:
        shutil.rmtree(shim, ignore_errors=True)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    sys.exit(main(args))
