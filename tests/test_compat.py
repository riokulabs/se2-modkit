import pytest

from se2modkit import compat


@pytest.fixture
def c():
    return compat.load_compat()


def test_compat_values(c):
    assert c.game_min == "4.0.0.3151"
    assert "4.0.0.3155" in c.game_versions_tested
    assert c.sdk_buildid_tested == 26083450


def test_untested_warns(c):
    w = compat.check_game("4.0.0.3160", c)
    assert len(w) == 1
    assert "se2mod deps check" in w[0]


def test_untested_strict_raises(c):
    with pytest.raises(compat.UntestedGameError):
        compat.check_game("4.0.0.3160", c, strict=True)


def test_tested_is_silent(c):
    assert compat.check_game("4.0.0.3155", c) == []


def test_below_min_strict_raises(c):
    with pytest.raises(compat.UntestedGameError):
        compat.check_game("4.0.0.3150", c, strict=True)


def test_unknown_installed_warns(c):
    w = compat.check_game(None, c)
    assert len(w) == 1
    assert "se2mod deps check" in w[0]


def test_version_tuple_orders_numerically():
    assert compat.version_tuple("4.0.0.3155") == (4, 0, 0, 3155)
    assert compat.version_tuple("4.0.0.999") < compat.version_tuple("4.0.0.3151")
