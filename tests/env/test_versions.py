from pathlib import Path

import fake_sdk
import pytest

from se2modkit.env.core import EnvError, discover_env
from se2modkit.env.versions import bundle_stamps, bundle_versions_from_dlls


def test_bundle_versions_from_synthetic_dll(tmp_path):
    dll = tmp_path / "Fake.dll"
    dll.write_bytes(b"\x01\x00\x05VRage\x0a4.0.0.3155\x00\x00")
    assert bundle_versions_from_dlls(dll) == {"VRage": "4.0.0.3155"}


def test_bundle_versions_skips_missing_and_length_mismatch(tmp_path):
    bad = tmp_path / "Bad.dll"
    bad.write_bytes(b"\x01\x00\x05VRage\x094.0.0.3155\x00\x00")  # declared length 9, actual 10
    assert bundle_versions_from_dlls(tmp_path / "absent.dll", bad) == {}


def test_bundle_versions_first_dll_wins(tmp_path):
    a = fake_sdk.write_dll(tmp_path / "a.dll", ("VRage", "1.0.0.1"))
    b = fake_sdk.write_dll(tmp_path / "b.dll", ("VRage", "2.0.0.2"), ("Game2", "2.0.0.2"))
    assert bundle_versions_from_dlls(a, b) == {"VRage": "1.0.0.1", "Game2": "2.0.0.2"}


def _env_with_sdk(tmp_path: Path):
    sdk = fake_sdk.make_sdk(tmp_path / "Space Engineers 2 - Mod SDK")
    return discover_env(flags={"sdk": str(sdk)}, environ={}, platform="linux", home=tmp_path / "home",
                        sysroot=tmp_path / "sys")


def test_bundle_stamps_shape(tmp_path):
    vrage, game = bundle_stamps(_env_with_sdk(tmp_path))
    assert vrage == {"System.Runtime": "1.0.0.0", "VRage": "4.0.0.3155"}
    assert game["Game2"] == "4.0.0.3155"
    assert game == {"Game2": "4.0.0.3155", **vrage}
    assert list(game) == ["Game2", "System.Runtime", "VRage"]


def test_bundle_stamps_without_sdk_is_plain_error(tmp_path):
    env = discover_env(environ={}, platform="linux", home=tmp_path, sysroot=tmp_path)
    with pytest.raises(EnvError, match=r"^Mod SDK not found"):
        bundle_stamps(env)
