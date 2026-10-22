"""`se2mod kit build`: render the Rioku atlas from its SVG sources into the user cache (PNG; DDS when a native
`texconv` is found). Nothing is written next to the sources."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import KIT_DIR, KitError, cache_dir
from . import atlas as atlas_mod

__all__ = ["BuildResult", "build", "find_texconv"]

INK = (255, 255, 255)        # one-colour decals: white ink, the alpha carries the artwork
DDS_FORMAT = "BC7_UNORM"
TEXCONV_TIMEOUT = 60.0       # s; texconv under Wine has hung before, so it never runs unbounded


@dataclass
class BuildResult:
    png: Path
    dds: Path | None = None
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def find_texconv(*, environ: Mapping[str, str] = os.environ, platform: str = sys.platform) -> Path | None:
    """A native `texconv` on PATH. Off Windows a `texconv.exe` is never used (it would need Wine)."""
    path = environ.get("PATH", "")
    if platform.startswith("win"):
        hit = shutil.which("texconv", path=path)
        return Path(hit) if hit else None
    for d in path.split(os.pathsep):
        p = Path(d) / "texconv"
        if d and p.is_file() and os.access(p, os.X_OK):
            return p
    return None


def _render(A: atlas_mod.Atlas, atlas_path: Path):
    from . import svg
    try:
        from PIL import Image
    except ImportError:
        raise KitError("Pillow is required for `se2mod kit build`: pip install 'se2-modkit[preview]'") from None
    out = Image.new("RGBA", A.size, (*INK, 0))
    for el in A.elements.values():
        src = atlas_path.parent.parent / el.source
        if not src.is_file():
            src = KIT_DIR / el.source
        x0, y0, x1, y1 = el.rect
        try:
            cell = svg.render(src.read_text(encoding="utf-8"), x1 - x0, y1 - y0, ink=INK)
        except svg.SvgError as e:
            raise KitError(f"Cannot render {el.source}: {e}.") from None
        out.paste(cell, (x0, y0))
    return out


def _texconv(exe: Path, png: Path, timeout: float) -> tuple[Path | None, str | None]:
    argv = [str(exe), "-nologo", "-y", "-f", DDS_FORMAT, "-m", "0", "-o", str(png.parent), str(png)]
    kw = {} if sys.platform.startswith("win") else {"start_new_session": True}
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    except OSError as e:
        return None, f"texconv could not start ({e}); wrote PNG only"
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if kw:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        proc.kill()
        proc.communicate()
        return None, f"texconv timed out after {timeout:g} s and was stopped; wrote PNG only"
    dds = png.with_suffix(".dds")
    if proc.returncode != 0 or not dds.is_file():
        tail = (out or b"").decode(errors="replace").strip().splitlines()[-1:]
        why = f": {tail[0]}" if tail else ""
        return None, f"texconv failed (exit {proc.returncode}){why}; wrote PNG only"
    return dds, None


def build(cache_root: Path | None = None, *, texconv: Path | str | None = "auto", timeout: float = TEXCONV_TIMEOUT,
          atlas_path: Path | None = None) -> BuildResult:
    """Render the atlas into `cache_dir(cache_root)`. `texconv="auto"` looks for a native texconv; None skips DDS."""
    atlas_path = atlas_mod.DEFAULT if atlas_path is None else Path(atlas_path)
    try:
        A = atlas_mod.load(atlas_path)
    except atlas_mod.AtlasError as e:
        raise KitError(f"{e}.") from None
    out_dir = cache_dir(cache_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{A.name}.png"
    tmp = png.with_name(png.name + ".part")
    _render(A, atlas_path).save(tmp, format="PNG", optimize=True)
    os.replace(tmp, png)
    res = BuildResult(png=png, files=[png])
    stale = png.with_suffix(".dds")
    if stale.exists():
        stale.unlink()  # never leave a DDS from an older build beside a new PNG
    exe = find_texconv() if texconv == "auto" else (Path(texconv) if texconv else None)
    if exe is None:
        if texconv is not None:
            res.warnings.append("no native texconv found; wrote PNG only (convert to DDS on Windows or install a "
                                "native texconv on PATH)")
        else:
            res.warnings.append("DDS skipped (no texconv requested); wrote PNG only")
        return res
    res.dds, warning = _texconv(exe, png, timeout)
    if res.dds:
        res.files.append(res.dds)
    if warning:
        res.warnings.append(warning)
    return res
