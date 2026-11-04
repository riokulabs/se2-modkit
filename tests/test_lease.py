"""`lease`: named, timed leases on shared files (a .blend two sessions both edit), held in the modkit cache."""
import pytest

from se2modkit import lease as L

T0 = 1_000_000.0


def test_take_records_the_holder(tmp_path):
    blend = tmp_path / "drive.blend"
    got = L.take(tmp_path / "cache", blend, owner="SE2 Mod Builder", ttl=3600, now=T0)
    assert (got.owner, got.path) == ("SE2 Mod Builder", str(blend.resolve()))
    assert [x.owner for x in L.active(tmp_path / "cache", now=T0 + 10)] == ["SE2 Mod Builder"]


def test_take_refuses_when_held_by_another_owner(tmp_path):
    blend = tmp_path / "drive.blend"
    L.take(tmp_path / "cache", blend, owner="A", ttl=3600, now=T0)
    with pytest.raises(L.LeaseHeld) as e:
        L.take(tmp_path / "cache", blend, owner="B", ttl=3600, now=T0 + 60)
    assert e.value.lease.owner == "A"


def test_the_same_owner_can_renew(tmp_path):
    blend = tmp_path / "drive.blend"
    L.take(tmp_path / "cache", blend, owner="A", ttl=60, now=T0)
    renewed = L.take(tmp_path / "cache", blend, owner="A", ttl=60, now=T0 + 50)
    assert renewed.since == T0 + 50


def test_lease_expires_after_ttl(tmp_path):
    blend = tmp_path / "drive.blend"
    L.take(tmp_path / "cache", blend, owner="A", ttl=60, now=T0)
    assert L.active(tmp_path / "cache", now=T0 + 61) == []
    assert L.take(tmp_path / "cache", blend, owner="B", ttl=60, now=T0 + 61).owner == "B"


def test_release_by_owner_only(tmp_path):
    blend = tmp_path / "drive.blend"
    L.take(tmp_path / "cache", blend, owner="A", ttl=3600, now=T0)
    with pytest.raises(L.LeaseHeld):
        L.release(tmp_path / "cache", blend, owner="B", now=T0)
    assert L.release(tmp_path / "cache", blend, owner="B", force=True, now=T0) is True
    assert L.active(tmp_path / "cache", now=T0) == []


def test_release_of_a_free_file_is_false(tmp_path):
    assert L.release(tmp_path / "cache", tmp_path / "free.blend", owner="A") is False


def test_two_paths_to_one_file_share_a_lease(tmp_path):
    (tmp_path / "d").mkdir()
    blend = tmp_path / "d" / "drive.blend"
    L.take(tmp_path / "cache", blend, owner="A", ttl=3600, now=T0)
    with pytest.raises(L.LeaseHeld):
        L.take(tmp_path / "cache", tmp_path / "d" / ".." / "d" / "drive.blend", owner="B", ttl=3600, now=T0)


@pytest.mark.parametrize("text, seconds", [("90", 90), ("30m", 1800), ("4h", 14400), ("1.5h", 5400)])
def test_parse_ttl(text, seconds):
    assert L.parse_ttl(text) == seconds


def test_parse_ttl_rejects_nonsense():
    with pytest.raises(ValueError):
        L.parse_ttl("soon")
