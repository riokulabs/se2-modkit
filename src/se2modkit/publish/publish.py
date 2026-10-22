"""Decide what a publish would do, print it, and (only with explicit approval) do it."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, gitutil, limits
from .changelog import ChangeNote, build_change_note
from ..env.procs import listing_available
from .hostenv import GAME_APP_ID, GAME_PROCS, HUB_PROCS, find_processes
from .metadata import (WorkshopMetadataInput, build_metadata_json, guid_to_steam_kv, hub_tags, has_uncertain_chars,
                       item_id_to_guid)
from .project import (ModProject, PublishConfig, Refusal, append_ledger, differs_from_head, last_published_commit,
                      ledger_item_ids, read_ledger, read_tracked, repo_rel, split_release_tag, write_mod_id)
from .stage import StageResult, dirty_shipping_inputs
from .steam import NEW_ITEM, Backend, DryRunBackend, plan_update

PLACEHOLDER_STEAM_ID = 0


@dataclass
class Decision:
    """Which Workshop item this publish targets, and why."""
    item_id: int | None            # None = create a new item
    adopted: bool = False
    reasons: list[str] = field(default_factory=list)


def decide_target(p: ModProject, ledger: list[dict], adopt_existing: bool) -> Decision:
    """Local rules (no Steam needed):
    * .vrgproj ModId recorded by us (in the ledger)      -> update it
    * .vrgproj ModId NOT in our ledger (the HUB made it) -> refuse unless --adopt-existing
    * no ModId, but the ledger has an id (crash between CreateItem and writing the .vrgproj) -> reuse that id
    * nothing                                            -> create a new item"""
    ours = ledger_item_ids(ledger)
    mod_id = p.mod_item_id
    if mod_id is not None:
        if mod_id in ours:
            return Decision(mod_id, reasons=[f"updating item {mod_id} (recorded in the publish ledger)"])
        if not adopt_existing:
            raise Refusal(f"{p.vrgproj.name} already maps project {p.guid} to Workshop item {mod_id}, which this "
                          "tool did not publish (the Mod HUB did, or another tool). Updating it replaces the HUB's "
                          "upload and from then on the HUB must never publish this project again. If that is what "
                          "you want, re-run with --adopt-existing.")
        return Decision(mod_id, adopted=True, reasons=[f"adopting HUB-published item {mod_id} (--adopt-existing)"])
    if ours:
        last = next(int(r["item_id"]) for r in reversed(ledger) if r.get("item_id"))
        return Decision(last, reasons=[f"ledger records item {last} but {p.vrgproj.name} has no ModId "
                                       "(an earlier run stopped after CreateItem); reusing it"])
    return Decision(None, reasons=["no Workshop item yet: a new one will be created"])


def check_remote(decision: Decision, remote, me: int, adopt_existing: bool, ours: set[int]) -> None:
    """Rules that need Steam: the KV-tag search must agree with the local decision."""
    for it in remote:
        if it.owner and it.owner != me:
            raise Refusal(f"Workshop item {it.item_id} with this project's VRageProjectID belongs to another "
                          "account; refusing (two projects must never share a GUID)")
        if decision.item_id is None or it.item_id != decision.item_id:
            if it.item_id in ours:
                continue
            if not adopt_existing:
                raise Refusal(f"Steam already has item {it.item_id} tagged with this project's VRageProjectID "
                              "(HUB-published?). Re-run with --adopt-existing to update it instead.")
    others = {it.item_id for it in remote} - ({decision.item_id} if decision.item_id else set())
    if decision.item_id is None and others:
        raise Refusal(f"refusing to create a second item for this project; existing: {sorted(others)}")


def change_note_for(p: ModProject, cfg: PublishConfig, version: str | None, ledger: list[dict],
                    override: Path | None, game_version: str | None = None) -> ChangeNote:
    data = read_tracked(p, cfg.changelog)
    return build_change_note(p.folder, cfg.changelog, version, last_published_commit(ledger),
                             changelog_text=data.decode("utf-8") if data is not None else None,
                             override_file=override, game_version=game_version)


@dataclass
class Plan:
    project: ModProject
    config: PublishConfig
    decision: Decision
    visibility: str | None           # None = keep the item's current visibility
    metadata_visibility: str
    description: str | None
    change_note: ChangeNote
    blockers: list[str]
    warnings: list[str]
    stage: StageResult | None
    bundles: dict[str, str]

    @property
    def ready(self) -> bool:
        return not self.blockers


def assemble(p: ModProject, cfg: PublishConfig, *, version: str | None, visibility: str | None,
             adopt_existing: bool, change_note_file: Path | None, keep_web_description: bool,
             stage: StageResult | None, bundles: dict[str, str], strict_git: bool,
             game_version: str | None = None) -> Plan:
    blockers: list[str] = []
    warnings: list[str] = list(cfg.problems)
    tag_mod, version = split_release_tag(version)
    if tag_mod is not None and tag_mod != p.name:
        raise Refusal(f"release tag {tag_mod}-v{version} is for mod '{tag_mod}', not '{p.name}'")
    if tag_mod is not None and not p.multi:
        raise Refusal(f"{p.name} is a single-mod repo: its release tags are vX.Y.Z, not {tag_mod}-v{version}")
    ledger = read_ledger(p)
    decision = decide_target(p, ledger, adopt_existing)
    blockers += limits.check_title(cfg.title)
    blockers += limits.check_summary(cfg.summary)
    if not cfg.tags:
        warnings.append("no tags set (the HUB allows that, but players filter by tag; e.g. tags = [\"Block\"])")
    description = None
    if keep_web_description:
        warnings.append("--keep-web-description: the Workshop description is left as it is")
    elif (desc_bytes := read_tracked(p, cfg.description)) is not None:
        description = desc_bytes.decode("utf-8")
        if differs_from_head(p, cfg.description):
            (blockers if strict_git else warnings).append(
                f"{repo_rel(p, cfg.description)} has uncommitted changes; the committed version is used")
        d_err, d_warn = limits.check_description(description)
        blockers += d_err
        warnings += d_warn
        if not description.strip():
            warnings.append(f"{cfg.description.name} is blank: the Workshop description is left unchanged "
                            "(same as the HUB with a blank Summary)")
    else:
        blockers.append(f"description file {repo_rel(p, cfg.description)} is not committed to git "
                        "(or pass --keep-web-description)")
    thumb = read_tracked(p, cfg.thumbnail)
    thumb_errs = limits.check_thumbnail(thumb) if thumb is not None else \
        [f"thumbnail {repo_rel(p, cfg.thumbnail)} is not committed to git"]
    if thumb_errs:
        blockers += thumb_errs
        blockers.append(f"fix with: se2publish thumb {p.repo.name if p.multi else p.folder.name}"
                        f"{f' --mod {p.name}' if p.multi else ''} --from <image> "
                        f"(re-encodes to {limits.THUMB_SIZE[0]}x{limits.THUMB_SIZE[1]} JPEG under 1 MB)")
    dirty = dirty_shipping_inputs(p)
    if dirty:
        msg = "uncommitted changes in files that ship: " + ", ".join(dirty[:8]) + (" ..." if len(dirty) > 8 else "")
        (blockers if strict_git else warnings).append(msg)
    for prob in cfg.problems:
        if strict_git and "not committed" in prob:
            blockers.append(prob)
    if has_uncertain_chars(cfg.title + cfg.summary):
        warnings.append("title/summary contain format characters whose JSON escaping could not be verified")
    if decision.item_id is None:
        vis = visibility or cfg.first_visibility
        meta_vis = vis
    else:
        vis = visibility
        last_vis = next((r.get("visibility") for r in reversed(ledger) if r.get("visibility")), None)
        meta_vis = visibility or last_vis or "Unlisted"
        if visibility is None:
            warnings.append("visibility: kept as it is on Steam (pass --visibility to change it); the metadata "
                            "copy is filled in from Steam at upload time")
    note = change_note_for(p, cfg, version, ledger, change_note_file, game_version)
    warnings += note.warnings
    if stage is not None:
        warnings += stage.warnings
        if not stage.scripts:
            warnings.append("no committed Scripts/*.cs: this publish is data-only")
    return Plan(p, cfg, decision, vis, meta_vis, description, note, blockers, warnings, stage, bundles)


def metadata_for(plan: Plan, item_id: int, author: int, visibility: str, now: str | None = None) -> str:
    kw = {}
    if now:
        kw["last_locally_updated"] = now
    return build_metadata_json(WorkshopMetadataInput(
        item_id=item_id, project_guid=plan.project.guid, author_steam_id=author, title=plan.config.title,
        summary=plan.config.summary, visibility=visibility, tags=plan.config.tags,
        project_version=plan.project.version, bundles=plan.bundles, dependencies=plan.config.dependencies, **kw))


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"... [{len(s) - n} more chars]"


def render_plan(plan: Plan, out=print, full: bool = False) -> list[tuple[str, object]]:
    """Print the whole plan, including every Steam call with its values. Returns the recorded calls."""
    p, cfg = plan.project, plan.config
    item = plan.decision.item_id
    out(f"== se2publish {__version__} - DRY RUN - {p.name} ({p.folder})")
    out(f"project GUID {p.guid}  ->  KV tag VRageProjectID={guid_to_steam_kv(p.guid)}")
    for r in plan.decision.reasons:
        out(f"target: {r}")
    if plan.stage:
        st = plan.stage
        m = st.manifest
        out(f"staged: {st.stage_dir}  commit {st.head[:12]}")
        out(f"manifest: {len(m.entries)} files, {m.total_bytes:,} bytes, sha256 {m.digest()[:16]}...")
        for top, (n, b) in m.by_top().items():
            out(f"  {top:<28} {n:>6} files {b:>14,} bytes")
        out(f"scripts from git HEAD: {len(st.scripts)}" + (f" ({', '.join(st.scripts[:4])}{' ...' if len(st.scripts) > 4 else ''})" if st.scripts else ""))
    else:
        out("staged: (not staged in this run)")
    if p.multi:
        out(f"mod {p.name} in multi-mod repo {p.repo}"
            + (f"; release tag {p.release_tag(plan.change_note.version)}" if plan.change_note.version else ""))
    out("change note source: " + plan.change_note.source)
    dry = DryRunBackend()
    dry.steam_id()
    dry.find_by_project_kv(guid_to_steam_kv(p.guid))
    target = item
    if item is None:
        target, _ = dry.create_item(GAME_APP_ID)
    meta = metadata_for(plan, target or 0, PLACEHOLDER_STEAM_ID, plan.metadata_visibility)
    content = plan.stage.stage_dir if plan.stage else Path("<stage dir>")
    preview = content / "thumb.jpg"
    up = plan_update(target or NEW_ITEM, guid_to_steam_kv(p.guid), cfg.title, hub_tags(cfg.tags), plan.visibility,
                     plan.description, content, preview, meta, plan.change_note.text)
    dry.update_item(GAME_APP_ID, up.item_id, up.calls, up.change_note)
    out("Steam calls (in order; nothing is sent in a dry run):")
    for i, (name, value) in enumerate(dry.log, 1):
        if name == "SetItemDescription":
            v = str(value)
            shown = v if full else _short(v, 400)
            out(f"  {i:>2}. {name}: {len(v.encode('utf-8'))} bytes, sha256 "
                f"{hashlib.sha256(v.encode()).hexdigest()[:16]}...\n{_indent(shown)}")
        elif name == "SetItemMetadata":
            out(f"  {i:>2}. {name}: {len(str(value).encode('utf-8'))} bytes (CRLF; Author and item id are filled "
                f"in at upload time{'; placeholder 0 here' if True else ''}):\n{_indent(str(value))}")
        elif name == "SubmitItemUpdate":
            out(f"  {i:>2}. {name}: change note ({len(str(value).encode('utf-8'))} bytes):\n{_indent(str(value))}")
        elif name == "StartItemUpdate" and value and value[1] == NEW_ITEM:
            out(f"  {i:>2}. {name}: ({value[0]}, <id from CreateItem>)")
        else:
            out(f"  {i:>2}. {name}: {value!r}")
    if plan.stage and plan.stage.captured_reference():
        out("  then: copy the captured reference cache back into the mod (as the HUB does): "
            + ", ".join(plan.stage.captured_reference()))
    if plan.decision.item_id is None:
        out(f"  then: write ModId into {p.vrgproj.name} (staged copy + canonical file, like the HUB's UpdateModId) "
            "and append to " + "docs/workshop/publish-ledger.jsonl")
    else:
        out("  then: append to docs/workshop/publish-ledger.jsonl")
    for w in plan.warnings:
        out(f"WARNING: {w}")
    for b in plan.blockers:
        out(f"BLOCKER: {b}")
    out(f"READY FOR UPLOAD: {'yes' if plan.ready else f'no ({len(plan.blockers)} blocker(s))'}")
    return dry.log


def _indent(s: str) -> str:
    return "\n".join("      | " + ln for ln in s.replace("\r\n", "\n").split("\n"))


# --- the real upload ---------------------------------------------------------------------------------------------

def execute(plan: Plan, backend: Backend, *, adopt_existing: bool, out=print, now: str | None = None) -> int:
    """Run the upload against a backend. The caller has already checked approval and confirmation."""
    if not plan.ready:
        raise Refusal("plan has blockers; fix them first")
    if plan.stage is None:
        raise Refusal("nothing staged")
    p = plan.project
    ledger = read_ledger(p)
    ours = ledger_item_ids(ledger)
    kv = guid_to_steam_kv(p.guid)
    me = backend.steam_id()
    remote = backend.find_by_project_kv(kv)
    check_remote(plan.decision, remote, me, adopt_existing, ours)
    stage_dir = plan.stage.stage_dir
    item = plan.decision.item_id
    meta_vis = plan.metadata_visibility
    if item is not None:
        cur = next((r for r in remote if r.item_id == item), None)
        if cur and cur.owner and cur.owner != me:
            raise Refusal(f"item {item} is owned by another account")
        if plan.visibility is None and cur and cur.visibility:
            meta_vis = cur.visibility
    common = {"tool": __version__, "commit": plan.stage.head, "project_guid": p.guid}
    if item is None:
        item, legal = backend.create_item(GAME_APP_ID)
        append_ledger(p, {**common, "event": "created", "item_id": item, "time": _utc()})
        out(f"created Workshop item {item}")
        write_mod_id(p, item)
        staged_proj = stage_dir / p.vrgproj.name
        staged_proj.write_bytes(p.vrgproj.read_bytes())
    meta = metadata_for(plan, item, me, meta_vis, now)
    up = plan_update(item, kv, plan.config.title, hub_tags(plan.config.tags), plan.visibility, plan.description,
                     stage_dir, stage_dir / "thumb.jpg", meta, plan.change_note.text)
    legal = backend.update_item(GAME_APP_ID, item, up.calls, up.change_note)
    # Re-hash: the staged .vrgproj may have just gained the ModId.
    from .stage import Limits, check_tree
    man, _ = check_tree(stage_dir, p, Limits(), {})
    append_ledger(p, {**common, "event": "published", "item_id": item, "time": _utc(),
                      "visibility": plan.visibility or meta_vis, "mod_version": plan.change_note.version,
                      "project_version": p.version, "manifest_sha256": man.digest(), "files": len(man.entries),
                      "bytes": man.total_bytes, "change_note_source": plan.change_note.source,
                      "adopted": plan.decision.adopted, "item_guid": item_id_to_guid(item)})
    for rel in plan.stage.captured_reference():
        dest = p.folder / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plan.stage.scratch / rel, dest)
        out(f"updated {rel} (captured reference cache; commit it so the next publish ships it)")
    out(f"published item {item}: https://steamcommunity.com/sharedfiles/filedetails/?id={item}")
    if legal:
        out("Steam says this account has not accepted the Workshop legal agreement: the item stays hidden until "
            "you accept it yourself on the item page.")
    out(f"commit {p.vrgproj.name} and docs/workshop/publish-ledger.jsonl in the mod repo.")
    return item


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def preflight_live(allow_running_game: bool = False, *, proc: Path | None = None,
                   environ: Mapping[str, str] = os.environ) -> None:
    """Before touching Steam for real: the game/HUB must be closed (same app id / shared prefix).

    Fails closed: if the platform gives no process list at all, nothing here can be checked, so it refuses.
    """
    if not listing_available(proc=proc):
        raise Refusal("cannot list running processes here, so the game and Mod HUB cannot be checked as closed; "
                      "publishing is refused")
    if not allow_running_game:
        extra = (environ["SE2_PROCESS_NAME"],) if environ.get("SE2_PROCESS_NAME") else ()
        busy = find_processes(GAME_PROCS + extra, proc=proc)
        if busy:
            raise Refusal("Space Engineers 2 is running (" + ", ".join(c[:60] for _, c in busy) + "); close it first")
    hub = find_processes(HUB_PROCS, proc=proc)
    if hub:
        raise Refusal("the Mod HUB/Editor is running; close it so it cannot publish the same project")
