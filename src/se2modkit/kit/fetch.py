"""`se2mod kit fetch`: download the kit asset zip of this modkit version and check its sha256 (`assets.toml`)."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from .. import __version__
from . import KIT_DIR, KitError, cache_dir

__all__ = ["fetch", "load_releases"]

ASSETS = KIT_DIR / "assets.toml"
SCHEMES = ("https://", "file://")
MAX_BYTES = 64 * 1024 * 1024        # download
MAX_UNPACKED = 256 * 1024 * 1024    # all members together, as unpacked


def load_releases(path: Path = ASSETS) -> dict[str, dict]:
    with Path(path).open("rb") as fh:
        return dict(tomllib.load(fh).get("releases", {}))


def _safe_members(z: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    out = []
    for info in z.infolist():
        name = PurePosixPath(info.filename)
        if name.is_absolute() or ".." in name.parts or "\\" in info.filename or ":" in info.filename:
            raise KitError(f"The kit zip has an unsafe member {info.filename!r}; refusing to unpack it.")
        if not info.is_dir():
            out.append(info)
    return out


def fetch(cache_root: Path | None = None, *, version: str = __version__, releases: dict | None = None) -> Path:
    """Download, verify and unpack the kit zip for `version` into `cache_dir(cache_root)`; returns that directory."""
    releases = load_releases() if releases is None else releases
    rel = releases.get(version)
    if not rel:
        raise KitError(f"There is no release of the kit assets for se2-modkit {version} yet; build them locally "
                       "with `se2mod kit build`.")
    url, want = str(rel.get("url", "")), str(rel.get("sha256", "")).lower()
    if not url.startswith(SCHEMES) or len(want) != 64:
        raise KitError(f"assets.toml has no valid url and sha256 for {version}.")
    out_dir = cache_dir(cache_root)
    with tempfile.TemporaryDirectory(prefix="se2modkit-kit-") as tmp:
        zpath = Path(tmp) / "kit.zip"
        h = hashlib.sha256()
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, zpath.open("wb") as fh:  # noqa: S310 (scheme checked)
                total = 0
                while chunk := resp.read(1 << 16):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise KitError(f"The kit zip at {url} is over {MAX_BYTES // (1024 * 1024)} MB; refusing it.")
                    h.update(chunk)
                    fh.write(chunk)
        except OSError as e:
            raise KitError(f"Cannot download {url} ({e}).") from None
        got = h.hexdigest()
        if got != want:
            raise KitError(f"The kit zip's sha256 is {got}, not the {want} recorded in assets.toml; not unpacked.")
        stage = Path(tmp) / "out"
        try:
            with zipfile.ZipFile(zpath) as z:
                members = _safe_members(z)
                if sum(i.file_size for i in members) > MAX_UNPACKED:
                    raise KitError(f"The kit zip unpacks to over {MAX_UNPACKED // (1024 * 1024)} MB; refusing it.")
                written = 0
                for info in members:
                    dest = stage / PurePosixPath(info.filename)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, dest.open("wb") as dst:
                        while chunk := src.read(1 << 16):
                            written += len(chunk)
                            if written > MAX_UNPACKED:  # the sizes in the zip's directory can lie
                                raise KitError("The kit zip unpacks to more than its directory says; refusing it.")
                            dst.write(chunk)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as e:
            raise KitError(f"The kit zip from {url} is not a readable zip ({e}).") from None
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                target = out_dir / p.relative_to(stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_name(target.name + ".part")
                shutil.copyfile(p, part)
                os.replace(part, target)  # a reader never sees a half-written asset
    return out_dir
