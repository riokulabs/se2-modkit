from pathlib import Path

import pytest

from se2modkit.env.config import ConfigError, Resolved, load_user_config, resolve, user_cache_dir, user_config_dir


def _resolve(flag, environ, config):
    return resolve(
        flag=flag,
        env_names=("SE2MODKIT_MODELS_DIR", "XX_MODELS_DIR"),
        config=config,
        config_key="models_dir",
        discover=lambda: "found",
        environ=environ,
    )


def test_precedence_flag_env_config_discovery():
    env = {"SE2MODKIT_MODELS_DIR": "e"}
    cfg = {"models_dir": "c"}
    assert _resolve("f", env, cfg) == Resolved("f", "flag")
    assert _resolve(None, env, cfg) == Resolved("e", "env")
    assert _resolve(None, {}, cfg) == Resolved("c", "config")
    assert _resolve(None, {}, {}) == Resolved("found", "discovered")
    r = resolve(flag=None, env_names=("X",), config={}, config_key="k", discover=lambda: None, environ={})
    assert r == Resolved(None, "missing")


def test_first_env_alias_wins():
    both = {"SE2MODKIT_MODELS_DIR": "new", "XX_MODELS_DIR": "old"}
    assert _resolve(None, both, {}).value == "new"
    assert _resolve(None, {"XX_MODELS_DIR": "old"}, {}) == Resolved("old", "env")


def test_config_dirs_linux_xdg():
    env = {"XDG_CONFIG_HOME": "/xdg/c", "XDG_CACHE_HOME": "/xdg/k"}
    assert user_config_dir(platform="linux", environ=env) == Path("/xdg/c/se2modkit")
    assert user_cache_dir(platform="linux", environ=env) == Path("/xdg/k/se2modkit")
    assert user_config_dir(platform="linux", environ={}) == Path.home() / ".config" / "se2modkit"
    assert user_cache_dir(platform="linux", environ={}) == Path.home() / ".cache" / "se2modkit"


def test_config_dirs_windows():
    env = {"APPDATA": "AppData-Roaming", "LOCALAPPDATA": "AppData-Local"}
    assert user_config_dir(platform="win32", environ=env) == Path("AppData-Roaming") / "se2modkit"
    assert user_cache_dir(platform="win32", environ=env) == Path("AppData-Local") / "se2modkit" / "Cache"


def test_config_env_override(tmp_path):
    other = tmp_path / "other.toml"
    other.write_text('models_dir = "x"\n', encoding="utf-8")
    assert load_user_config(environ={"SE2MODKIT_CONFIG": str(other)}) == {"models_dir": "x"}
    assert load_user_config(tmp_path / "missing.toml", environ={}) == {}
    explicit = tmp_path / "e.toml"
    explicit.write_text('a = 1\n', encoding="utf-8")
    assert load_user_config(explicit, environ={}) == {"a": 1}


def test_malformed_user_config_is_a_plain_error(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("models_dir = = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="bad.toml"):
        load_user_config(bad, environ={})
