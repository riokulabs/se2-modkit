"""Leak gate for the diag package: no home-path literals, no machine-specific install paths, no token-like values,
and no default location for the bridge plugin's config file (it is named only by SE2_MCP_CFG or config)."""
import re
from pathlib import Path

import se2modkit.diag

DIAG = Path(se2modkit.diag.__file__).parent
FILES = sorted(p for p in DIAG.rglob("*") if p.suffix in (".py", ".csx"))

FORBIDDEN = [
    (r"Path\.home\(\)", "Path.home(): take locations from SE2Env"),
    (r"expanduser\(\s*['\"]~", "a literal ~ path"),
    (r"/home/", "a /home/ path"),
    (r"debian-installation", "a machine-specific Steam root"),
    (r"\.steam/", "a literal Steam path"),
    (r"GE-Proton", "a Proton build name"),
    (r"[/\\][^/\\\s'\"]+\.cfg\b|\b[A-Z][A-Za-z0-9_-]*\.cfg\b", "a plugin config file name"),
    (r"\.config/Pulsar", "a literal Pulsar path"),
    (r"Bearer\s+[A-Za-z0-9]", "a literal bearer token"),
    (r"<SecretKey>[^<(\[]", "a literal SecretKey value"),
    (r"\b[0-9a-fA-F]{32,}\b", "a long hex literal (token-like)"),
]


def test_diag_uses_env_paths():
    assert FILES
    bad = []
    for p in FILES:
        text = p.read_text(encoding="utf-8")
        for rx, why in FORBIDDEN:
            for m in re.finditer(rx, text):
                line = text.count("\n", 0, m.start()) + 1
                bad.append(f"{p.relative_to(DIAG)}:{line}: {why}: {m.group(0)!r}")
    assert not bad, "\n".join(bad)


def test_probes_are_package_data():
    from se2modkit.diag import locations
    assert (locations.PROBES_DIR / "_prelude.csx").is_file()
    assert locations.PROBES_DIR.parent == DIAG


def test_se2diag_script_and_package_data():
    import subprocess
    import sys
    import tomllib
    data = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["se2diag"] == "se2modkit.diag.cli:main"
    assert "diag" in data["project"]["optional-dependencies"]
    assert "diag/probes/*.csx" in data["tool"]["setuptools"]["package-data"]["se2modkit"]
    r = subprocess.run([sys.executable, "-m", "se2modkit.diag", "--version"], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0 and r.stdout.startswith("se2diag ")
