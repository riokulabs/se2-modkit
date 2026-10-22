"""Synthetic .NET assembly stand-ins: just the Bundle attribute blob, no real assembly bytes."""

from __future__ import annotations

from pathlib import Path


def bundle_blob(name: str, version: str) -> bytes:
    """The custom-attribute blob shape `[assembly: Bundle(name, version)]` leaves in a DLL."""
    n, v = name.encode(), version.encode()
    return b"\x01\x00" + bytes([len(n)]) + n + bytes([len(v)]) + v + b"\x00\x00"


def write_dll(path: Path, *bundles: tuple[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ-fake\x00" + b"".join(bundle_blob(n, v) for n, v in bundles) + b"\x00tail")
    return path


def make_sdk(sdk_dir: Path, version: str = "4.0.0.3155") -> Path:
    """An SDK dir with Editor assemblies carrying VRage/Game2 bundles and an empty Vanilla Assets tree."""
    ed = sdk_dir / "Editor"
    write_dll(ed / "VRage.Core.dll", ("VRage", version))
    write_dll(ed / "Game2.Simulation.dll", ("Game2", version), ("VRage", version))
    (sdk_dir / "GameData" / "Vanilla" / "Assets").mkdir(parents=True, exist_ok=True)
    return sdk_dir


def make_game(game_dir: Path, version: str = "4.0.0.3155") -> Path:
    write_dll(game_dir / "Game2" / "VRage.Core.dll", ("VRage", version))
    write_dll(game_dir / "Game2" / "Game2.Simulation.dll", ("Game2", version), ("VRage", version))
    return game_dir
