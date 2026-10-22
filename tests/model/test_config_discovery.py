import inspect

from se2modkit.model import config


def test_sdk_from_env_not_literal(monkeypatch, tmp_path):
    assert "debian-installation" not in inspect.getsource(config)
    assets = tmp_path / "SDK" / "GameData" / "Vanilla" / "Assets"
    assets.mkdir(parents=True)
    monkeypatch.setenv("SE2_SDK_ASSETS", str(assets))
    monkeypatch.setenv("SE2MODKIT_CONFIG", str(tmp_path / "none.toml"))
    assert config.SDK == str(tmp_path / "SDK")
    assert config.VANILLA_ASSETS == str(assets)


def test_vanilla_ref_from_config_key(monkeypatch, tmp_path):
    monkeypatch.setenv("SE2MODKIT_CONFIG", str(tmp_path / "none.toml"))
    assert config.VANILLA_REF is None
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'vanilla_ref = "{(tmp_path / "ref").as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv("SE2MODKIT_CONFIG", str(cfg))
    assert config.VANILLA_REF == str(tmp_path / "ref")
