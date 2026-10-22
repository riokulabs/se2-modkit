"""Target selection, visibility defaults, refusals, the fake-Steam upload, the ledger and the approval gate."""
import json

import pytest

from se2modkit.publish import cli, steam
from se2modkit.publish.metadata import guid_to_steam_kv, item_id_to_guid
from se2modkit.publish.project import Refusal, append_ledger, load_config, load_project, read_ledger, read_tracked
from se2modkit.publish.publish import assemble, decide_target, execute, render_plan
from se2modkit.publish.stage import Limits, finish_stage
from se2modkit.publish.steam import DryRunBackend, FakeBackend

from pubfix import GUID, git, vrgproj
from test_stage_rules import fake_builder_output

BUNDLES = {"Game2": "4.0.0.3151", "System.Runtime": "1.0.0.0", "VRage": "4.0.0.3151"}


def make_plan(mod, tmp_path, **kw):
    p = load_project(mod)
    cfg = load_config(p)
    st = finish_stage(p, fake_builder_output(mod, tmp_path / "stage"), read_tracked(p, cfg.thumbnail), git(mod, "rev-parse", "HEAD").strip(),
                      Limits())
    args = dict(version=None, visibility=None, adopt_existing=False, change_note_file=None,
                keep_web_description=False, stage=st, bundles=BUNDLES, strict_git=True)
    args.update(kw)
    return assemble(p, cfg, **args)


def set_mod_id(mod, item):
    (mod / "TestMod.vrgproj").write_text(vrgproj("TestMod", mod_id=item_id_to_guid(item)))
    git(mod, "commit", "-qam", "mod id")


# --- target & visibility -----------------------------------------------------------------------------------------

def test_new_item_defaults_to_unlisted(mod, tmp_path):
    plan = make_plan(mod, tmp_path)
    assert plan.decision.item_id is None and plan.visibility == "Unlisted" and plan.ready, plan.blockers


def test_new_item_visibility_override(mod, tmp_path):
    assert make_plan(mod, tmp_path, visibility="Private").visibility == "Private"


def test_update_keeps_visibility_unless_asked(mod, tmp_path):
    set_mod_id(mod, 3900000123)
    append_ledger(load_project(mod), {"event": "published", "item_id": 3900000123, "visibility": "Unlisted"})
    plan = make_plan(mod, tmp_path)
    assert plan.decision.item_id == 3900000123 and plan.visibility is None
    assert plan.metadata_visibility == "Unlisted"
    assert make_plan(mod, tmp_path, visibility="Public").visibility == "Public"


def test_hub_published_item_needs_adopt(mod, tmp_path):
    set_mod_id(mod, 3700000001)       # ModId present, not in our ledger -> the HUB published it
    with pytest.raises(Refusal, match="--adopt-existing"):
        make_plan(mod, tmp_path)
    plan = make_plan(mod, tmp_path, adopt_existing=True)
    assert plan.decision.item_id == 3700000001 and plan.decision.adopted


def test_ledger_id_without_mod_id_is_reused(mod):
    p = load_project(mod)
    append_ledger(p, {"event": "created", "item_id": 3900000999})
    d = decide_target(p, read_ledger(p), False)
    assert d.item_id == 3900000999


def test_blockers(mod, tmp_path):
    (mod / "docs/workshop/description.bbcode").unlink()
    (mod / "docs/workshop/publish.toml").write_text('title = "' + "x" * 60 + '"\n')
    git(mod, "commit", "-qam", "drop description, long title")
    (mod / "Assets/Extra.def").write_text("{}")
    plan = make_plan(mod, tmp_path)
    text = "\n".join(plan.blockers)
    assert "description file" in text and "50" in text and "uncommitted changes" in text
    assert not plan.ready
    # in a dry run uncommitted changes are only a warning
    plan = make_plan(mod, tmp_path, strict_git=False)
    assert not any("uncommitted" in b for b in plan.blockers)
    assert any("uncommitted" in w for w in plan.warnings)


def test_blank_description_is_not_sent(mod, tmp_path):
    (mod / "docs/workshop/description.bbcode").write_text("  \n")
    git(mod, "commit", "-qam", "blank")
    lines = []
    calls = render_plan(make_plan(mod, tmp_path), out=lines.append)
    assert "SetItemDescription" not in [c[0] for c in calls]


# --- dry run output ----------------------------------------------------------------------------------------------

def test_dry_run_prints_every_call_and_the_exact_change_note(mod, tmp_path):
    plan = make_plan(mod, tmp_path)
    lines = []
    calls = render_plan(plan, out=lines.append)
    names = [c[0] for c in calls]
    assert names == ["SteamUser.GetSteamID", "QueryUGC(VRageProjectID)", "CreateItem", "StartItemUpdate",
                     "RemoveItemKeyValueTags", "AddItemKeyValueTag", "SetItemTitle", "SetItemTags",
                     "SetItemVisibility", "SetItemDescription", "SetItemContent", "SetItemPreview",
                     "SetItemMetadata", "SubmitItemUpdate"]
    out = "\n".join(lines)
    assert dict(calls)["AddItemKeyValueTag"] == ("VRageProjectID", guid_to_steam_kv(GUID))
    assert dict(calls)["SetItemTags"] == ["ModTagBlock", "UGCModTypeTag"]
    for ln in plan.change_note.text.split("\n"):
        assert f"      | {ln}" in out
    assert plan.change_note.text.startswith("v1.1.0 - 2026-10-20")
    assert "READY FOR UPLOAD: yes" in out


# --- execution against the fake Workshop -------------------------------------------------------------------------

NOW = "2026-10-20T05:10:19.0179622-07:00"


def test_first_publish_creates_unlisted_item_and_records_everything(mod, tmp_path):
    plan = make_plan(mod, tmp_path)
    fake = FakeBackend()
    item = execute(plan, fake, adopt_existing=False, out=lambda *_: None, now=NOW)
    it = fake.items[item]
    assert it["visibility"] == "Unlisted"
    assert it["kv"] == {"VRageProjectID": guid_to_steam_kv(GUID)}
    assert it["SetItemTitle"] == "Test Mod by Rioku Labs"
    assert it["change_notes"] == [plan.change_note.text]
    meta = json.loads(it["SetItemMetadata"])
    assert meta["$Value"]["Id"] == item and meta["$Value"]["Author"] == fake.me
    assert meta["$Value"]["FullMetadata"]["ProjectID"]["ModId"]["Id"] == item_id_to_guid(item)
    assert "\r\n" in it["SetItemMetadata"]
    # the .vrgproj (canonical + staged) now holds the ModId, like the HUB's UpdateModId
    p = load_project(mod)
    assert p.mod_item_id == item
    assert (plan.stage.stage_dir / "TestMod.vrgproj").read_bytes() == (mod / "TestMod.vrgproj").read_bytes()
    rows = read_ledger(p)
    assert [r["event"] for r in rows] == ["created", "published"]
    pub = rows[-1]
    assert pub["item_id"] == item and pub["commit"] == plan.stage.head and len(pub["manifest_sha256"]) == 64
    assert pub["visibility"] == "Unlisted" and pub["mod_version"] == "1.1.0"


def test_update_after_first_publish_does_not_touch_visibility(mod, tmp_path):
    plan = make_plan(mod, tmp_path)
    fake = FakeBackend()
    item = execute(plan, fake, adopt_existing=False, out=lambda *_: None, now=NOW)
    git(mod, "commit", "-qam", "record publish")
    fake.items[item]["visibility"] = "Public"              # the user made it public on the web
    plan_again = make_plan(mod, tmp_path / "2")
    assert plan_again.decision.item_id == item
    execute(plan_again, fake, adopt_existing=False, out=lambda *_: None, now=NOW)
    assert fake.items[item]["visibility"] == "Public"
    assert len([c for c in fake.calls if c[0] == "CreateItem"]) == 1


def test_remote_duplicate_refused(mod, tmp_path):
    plan = make_plan(mod, tmp_path)
    fake = FakeBackend(items={3700000005: {"kv": {"VRageProjectID": guid_to_steam_kv(GUID)}}})
    with pytest.raises(Refusal):
        execute(plan, fake, adopt_existing=False, out=lambda *_: None)
    assert not any(c[0] == "CreateItem" for c in fake.calls)


def test_item_owned_by_someone_else_refused(mod, tmp_path):
    set_mod_id(mod, 3700000001)
    plan = make_plan(mod, tmp_path, adopt_existing=True)
    fake = FakeBackend(items={3700000001: {"owner": 42, "kv": {"VRageProjectID": guid_to_steam_kv(GUID)}}})
    with pytest.raises(Refusal, match="another account"):
        execute(plan, fake, adopt_existing=True, out=lambda *_: None)


def test_adopt_existing_hub_item(mod, tmp_path):
    set_mod_id(mod, 3700000001)
    fake = FakeBackend(items={3700000001: {"kv": {"VRageProjectID": guid_to_steam_kv(GUID)}, "visibility": "Public"}})
    plan = make_plan(mod, tmp_path, adopt_existing=True)
    execute(plan, fake, adopt_existing=True, out=lambda *_: None, now=NOW)
    assert read_ledger(load_project(mod))[-1]["adopted"] is True
    meta = json.loads(fake.items[3700000001]["SetItemMetadata"])
    assert meta["$Value"]["FullMetadata"]["BaseMetadata"]["Visiblity"] == "Public"


def test_execute_refuses_plan_with_blockers(mod, tmp_path):
    (mod / "docs/workshop/description.bbcode").unlink()
    plan = make_plan(mod, tmp_path)
    with pytest.raises(Refusal, match="blockers"):
        execute(plan, FakeBackend(), adopt_existing=False)


# --- the approval gate (CLI) -------------------------------------------------------------------------------------

class Boom(Exception):
    pass


@pytest.fixture
def no_real_steam(monkeypatch, mod, tmp_path):
    def explode(*a, **k):
        raise Boom("the real Steam backend must not be constructed")
    monkeypatch.setattr(steam, "FlatApiBackend", explode)
    st = None

    def fake_do_stage(args, p, cfg, env):
        from se2modkit.publish.stage import finish_stage as fs
        return fs(p, fake_builder_output(mod, tmp_path / "stage"), read_tracked(p, cfg.thumbnail),
                  git(mod, "rev-parse", "HEAD").strip(), Limits())
    monkeypatch.setattr(cli, "do_stage", fake_do_stage)
    return mod


def test_cli_publish_is_dry_run_by_default(no_real_steam, capsys):
    rc = cli.main(["publish", str(no_real_steam)])
    out = capsys.readouterr().out
    assert rc == 0 and "DRY RUN" in out and "SubmitItemUpdate" in out


def test_cli_real_upload_needs_a_terminal_confirmation(no_real_steam, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = cli.main(["publish", str(no_real_steam), "--i-have-user-approval"])
    assert rc == 2 and "interactive terminal" in capsys.readouterr().err


def test_cli_real_upload_wrong_confirmation_sends_nothing(no_real_steam, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "yes")
    rc = cli.main(["publish", str(no_real_steam), "--i-have-user-approval"])
    assert rc == 2 and "not confirmed" in capsys.readouterr().out


def test_cli_refuses_worktree(mod, capsys):
    wt = mod.parent / "TestMod-wt"
    git(mod, "worktree", "add", "-q", str(wt))
    assert cli.main(["publish", str(wt), "--no-stage"]) == 3
    assert "worktree" in capsys.readouterr().err


def test_steam_callback_struct_layouts_match_linux_pack4():
    import ctypes
    from se2modkit.publish.steam import (_APICallCompleted, _CallbackMsg, _CreateItemResult, _QueryCompleted,
                                  _SubmitItemUpdateResult)
    assert ctypes.sizeof(_CallbackMsg) == 20 and _CallbackMsg.cubParam.offset == 16
    assert ctypes.sizeof(_APICallCompleted) == 16
    assert ctypes.sizeof(_CreateItemResult) == 16 and _CreateItemResult.nPublishedFileId.offset == 4
    assert ctypes.sizeof(_SubmitItemUpdateResult) == 16 and _SubmitItemUpdateResult.nPublishedFileId.offset == 8
    assert ctypes.sizeof(_QueryCompleted) == 280


def test_dry_run_backend_never_loads_steam(monkeypatch):
    import ctypes
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: (_ for _ in ()).throw(AssertionError("CDLL loaded")))
    b = DryRunBackend()
    b.steam_id(); b.find_by_project_kv("x"); b.create_item(1133870)
    b.update_item(1133870, 0, [("SetItemTitle", "t")], "note")
    assert [c[0] for c in b.log][-2:] == ["SetItemTitle", "SubmitItemUpdate"]


def test_captured_reference_cache_is_copied_back_after_upload(mod, tmp_path):
    plan = make_plan(mod, tmp_path)
    scratch = tmp_path / "scratch"
    (scratch / "Assets").mkdir(parents=True)
    (scratch / "Assets/contentcache_reference.vrb").write_bytes(b"ref")
    (scratch / "Assets/contentcache_reference.vrb.meta").write_text("{}")
    plan.stage.scratch = scratch
    lines = []
    render_plan(plan, out=lines.append)
    assert any("reference cache back" in ln for ln in lines)
    assert not (mod / "Assets/contentcache_reference.vrb").exists()      # dry run touches nothing
    execute(plan, FakeBackend(), adopt_existing=False, out=lambda *_: None, now=NOW)
    assert (mod / "Assets/contentcache_reference.vrb").read_bytes() == b"ref"
