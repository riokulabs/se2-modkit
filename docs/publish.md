# Workshop publisher (`se2publish`, extra `publish`)

A standalone Steam Workshop publisher for Space Engineers 2 mods. It exists for one reason: the official Mod HUB
never uploads `Scripts/`, so scripted mods need another route. It was the separate `se2publish` tool and now lives in
`se2modkit.publish`; the command line and every safety gate are unchanged.

Built for game 4.0.0.3151 (Steam app 1133870) and Mod SDK build 26049505 (app 3578990) on Linux. Re-verify after any
game or SDK update (see "When the game updates").

> **Risk note: read before the first real upload.**
>
> * This is an **unofficial** upload route outside Keen's official SDK publishing (the Mod HUB). Keen's SE2 EULA §9
>   says UGC "may only be created or modified using the officially designated Mod SDK platform". The build and
>   validation still run through Keen's own SDK builder, but the upload does not, so this sits in a gray area. A
>   scripted SE2 item uploaded this way has been public since July 2026 without action from Valve or Keen; that is
>   "tolerated so far", not "approved".
> * It re-implements Keen's Workshop metadata format. A game update can change that format; then the game may stop
>   recognizing items uploaded by this tool until it is updated.
> * **Never publish the same project from the Mod HUB afterwards.** The HUB would replace the item with a script-less
>   copy (and would force it Public on a first publish). Pick one route per project and stay with it.
> * Nothing is uploaded unless you pass `--i-have-user-approval` **and** type the mod's name at the prompt.

## What it does

```
<mod repo> (canonical git repo; in a multi-mod repo, the mod's folder)
  -> release-build check: a `se2mod build-models MOD --release` stamp of this very commit (mods built through modkit)
  -> scratch copy (Assets/, Content/, Worlds/, .vrgproj, build caches; never Scripts/, Source/, Tests/, docs/, .git)
  -> SDK builder under Proton: build, then --prepareForPublish --captureReferenceCache   (Keen's own validation)
  -> + Scripts/**/*.cs from git HEAD (never untracked or modified files) + thumb.jpg
  -> safety checks on every staged file -> manifest (paths, sizes, sha256, total)
  -> plan: every ISteamUGC call with its values (DRY RUN, the default)
  -> real upload only with --i-have-user-approval + typed confirmation, through the logged-in Steam client
  -> ledger entry in the mod folder (item id, version, commit, manifest hash)
```

## Install

`pip install se2-modkit[publish]` gives two equivalent commands: `se2publish ...` and `se2mod publish ...`.

* Python 3.11+, git. Pillow only for `se2publish thumb`.
* The Steam client (native Linux), logged in. The tool never asks for, reads or stores Steam credentials.
* The SE2 game and the Mod SDK installed through Steam, and the Proton build the SDK uses. They are found like every
  other modkit command (`se2mod env`): flags, then `SE2_STEAM_ROOT`, `SE2_SDK_ASSETS`, `SE2_PROTON`, then the user
  config, then discovery.
* A native `libsteam_api.so` (the Steamworks redistributable). The tool **does not ship one**; it finds the copy
  Pulsar installs (`Libraries/Modern/libsteam_api.so` next to Pulsar). Override with `SE2_LIBSTEAM_API`.

A bare mod name is looked up in the user config's `workspace_mods` folders, then `$SE2_WORKSPACE/mods`; otherwise
pass a path. `--work-dir` defaults to `~/.cache/se2publish/<Mod>` (`<repo>--<Mod>` for a multi-mod repo); keep it
short: the SDK's model importer fails on Windows paths of 260+ characters.

## Per-mod files (in the mod's own repo)

| File | Purpose |
|---|---|
| `docs/workshop/publish.toml` | title, tags, summary, file locations, dependencies, change-note skip list (all optional) |
| `docs/workshop/description.bbcode` | the Workshop description (Steam BBCode, ≤ 8000 bytes counting newlines as CRLF) |
| `CHANGELOG.md` | Keep a Changelog; the section for the published version becomes the change note |
| `docs/workshop/thumb.jpg` | preview image, JPEG < 1 MB (1280×720 recommended) |
| `docs/workshop/publish-ledger.jsonl` | written by the tool; commit it |

```toml
# docs/workshop/publish.toml
title = "Demo Mod by Rioku Labs"      # <= 50 characters (the HUB's limit)
tags = ["Block"]                          # SE2's fixed set: Armor Audio Asteroid Block Character Material Particles Skybox Other
summary = ""                              # <= 250 chars; goes into Keen's metadata (the HUB "Summary"); keep it blank
description = "docs/workshop/description.bbcode"
changelog = "CHANGELOG.md"
thumbnail = "docs/workshop/thumb.jpg"
first_visibility = "Unlisted"             # first upload only
change_note_skip_sections = ["For modders", "Documentation", "Development"]   # CHANGELOG ### subsections left out of the change note (case-insensitive; [] keeps all)
dependencies = []                         # Workshop item ids this mod requires (Keen metadata "Dependencies")
```

All paths are relative to the mod's folder. `dependencies` fills `BaseMetadata.Dependencies` with
`{Id = <item GUID>, ServiceName = "Steam"}` entries (null when empty, as the HUB writes it); it does not set Steam's
"Required items" list.

### Multi-mod repos

A repo whose `pyproject.toml` (read from git HEAD) has `[tool.se2modkit.mods.<Name>] path = "mods/<Name>"` tables
holds several mods. Each mod keeps its own files under its folder: `mods/<Name>/CHANGELOG.md`,
`mods/<Name>/docs/workshop/...`, its own ledger. Release tags are `<Name>-vX.Y.Z` (single-mod repos keep `vX.Y.Z`).

Pick the mod with `--mod Name`, with a release tag as `--version Name-vX.Y.Z`, or by pointing at the mod's folder.
Run against the repo root with none of these and the tool refuses with "which mod?" and the list of names; it never
picks the first one. The table's name, the folder name, the `.vrgproj` name and the project Name must all match.

## Usage

```bash
# The clean model build a publish needs (mods with a Source/Model build script; see incremental-builds.md)
se2mod build-models DemoMod --release

# Build + stage + check, print the manifest (add --list for every file)
se2publish stage DemoMod

# The plan: stages again (or --reuse-stage) and prints every Steam call with values, including the exact change note
se2publish publish DemoMod --version 1.0.0
se2publish publish DemoMod --reuse-stage --version 1.0.0 --full     # full description text too
se2publish publish ModPack --version Core-v1.2.0                       # multi-mod repo: mod Core, mods/Core/CHANGELOG.md

# Re-encode a thumbnail (cover-crop 1280x720, JPEG under 1 MB)
se2publish thumb DemoMod --from ~/Pictures/hero.png

# The real upload (asks you to type the mod name; refuses on any blocker)
se2publish publish DemoMod --version 1.0.0 --i-have-user-approval

se2publish ledger DemoMod
```

`se2mod publish <same arguments>` runs the same code.

### Fields, and how they differ from the HUB

| Steam call | Value | HUB behavior (4.0 code) |
|---|---|---|
| `AddItemKeyValueTag` | `VRageProjectID = <project GUID with '_' for '-'>` (removed first) | same |
| `SetItemTitle` | `title` | same (Name) |
| `SetItemTags` | tags + `UGCModTypeTag` | same |
| `SetItemVisibility` | first upload: `Unlisted` (or `--visibility`); updates: **not sent** unless `--visibility` | HUB: first upload forced Public; updates re-send the current one |
| `SetItemDescription` | `description.bbcode` if not blank; `--keep-web-description` to skip | HUB sends its 250-char Summary when not blank |
| `SetItemContent` / `SetItemPreview` | the staged folder / its `thumb.jpg` | same layout, plus `Scripts/` |
| `SetItemMetadata` | Keen's `SteamUgcMetadata` JSON (see below) | same format |
| `SubmitItemUpdate` | change note from `CHANGELOG.md` | HUB always sends an empty note |

**Metadata.** Byte-for-byte what the HUB's serializer writes: the `$Bundles`/`$Type`/`$Value` archive envelope,
2-space indentation, CRLF (the HUB runs under Proton/Windows), no trailing newline, .NET's relaxed JSON escaping.
`Version` is the `.vrgproj` `Version` (null in all current mods), `LastLocallyUpdated` is the local time at upload,
`Author` is the logged-in SteamID, and the summary (not the description) goes into `BaseMetadata.Description`, as with
the HUB; that keeps the metadata under Steam's 5000-byte limit. The writer is checked against every
`.steam-ugc-metadata` file the game itself has cached on this machine (97/97 identical) and against a hand-written
fixture.

**Change notes** come from the `CHANGELOG.md` section for `--version` (default: the newest released section). The
first line is `v1.2.0 - 2026-10-20`, then the section converted to BBCode with the small converter
in `se2publish/bbcode.py` (only tags that render on the Change Notes tab). If the section is missing, the note
summarizes the git commits since the last published commit in the ledger.
Notes over 2000 bytes warn; over 8000 bytes (Valve documents no limit) they are trimmed at a line boundary with a
warning. CHANGELOG `###` subsections named in `change_note_skip_sections` in `publish.toml` (default: For modders,
Documentation, Development; matched case-insensitively; `[]` keeps everything) are left out of the note, and the dry
run lists what was dropped. `--change-note-file` overrides (`.md` is converted, `.bbcode` is sent as is).

### Safety rules

* **Dry run by default.** It prints every Steam call it would make with its values and loads nothing from Steam.
* **Real upload** needs `--i-have-user-approval`, an interactive terminal, the full manifest listing, and the mod
  name typed back. It also refuses while SE2 (`Modern.bin`/`SpaceEngineers2.exe`) or the HUB/Editor is running.
* **Release build required.** A mod that builds its models through se2modkit (a `[tool.se2modkit]` project with a
  model build script or model files, whatever the script is named) publishes only models from a release build
  (`se2mod build-models MOD --release`). Its stamp must be this mod's and name the commit HEAD points at; the tree
  must be clean (files git ignores under the models folder or `Assets/` count, they would ship); the build script it
  recorded must still be there; nothing under the models folder or `Assets/` may be a symlink; every model file must
  still be the one the release build stamped; and every model file under `Assets/<mod>/` (what the publish stages
  from) must be the release build's export copied to the same place in its block, or for a `.model` the staged
  package's file at that place (an older copy, or the models of a renamed or removed block, are refused by name).
  Otherwise a dry run shows a `release build:` blocker and a warning, and a real upload is refused before anything
  is staged, in one sentence naming the command to run. A mod with a build script but no `[tool.se2modkit]` project
  builds its models its own way, without the incremental build state: it is not refused, but the plan carries a
  warning that its models are not verified by a release build. Mods with no model build are not affected.
* **Canonical folder only.** Refused: git worktrees (e.g. `mods/DemoMod-dmg`; on purpose, see below), folders whose
  name, `.vrgproj` and project Name differ, non-git folders. Other folders sharing the GUID are listed.
* **No stealing the HUB's item.** If the `.vrgproj` already has a `ModId` that is not in this tool's ledger (the HUB
  published it), the tool refuses unless `--adopt-existing`. Before uploading it also searches Steam for items tagged
  with the project's `VRageProjectID` and refuses on any item it does not own, and on duplicates.
* **What can ship:** top level `Assets/ Assets.meta Content/ Worlds/ Scripts/ <Mod>.vrgproj thumb.jpg` only. Refused
  anywhere: `Tests/ Source/ docs/ .git/ previews/ decompiled/ bin/ obj/ …`, `.dll .exe .pdb .so .py .sh .md .blend
  .env .pem .key .zip …`, PE/ELF binaries by content (Keen DLLs), C# outside `Scripts/`, decompiler markers in
  scripts (`ILSpy`, `// Decompiled with`, `_003C`, `C:\BuildAgent\`), preview renders, git-lfs pointers, things that
  look like secrets (private keys, GitHub/AWS/Slack/Anthropic tokens, `steamLoginSecure`), symlinks, files over
  256 MB, more than 2 GB or 50 000 files in total.
* **Config paths** (`description`, `changelog`, `thumbnail`) must be relative paths inside the mod repo: absolute
  paths, `~`, `..`, anything resolving outside the repo (symlinks included) or into `.git` are refused. The files
  are read from **git HEAD** (committed symlinks and lfs pointers refused); an untracked description or thumbnail
  is a blocker, an uncommitted edit is a warning in a dry run and a blocker for a real upload. `publish.toml`
  itself is read from HEAD when committed.
* **Scripts** ship only from git HEAD; uncommitted or untracked script changes are reported as "NOT shipped".
  For a real upload, any uncommitted change to shipped inputs (`Assets/`, `Worlds/`, `.vrgproj`, `Scripts/`) is a
  blocker.
* **Ledger:** every create and publish appends a JSON line (item id, visibility, mod version, commit, manifest
  sha256, file count, bytes, change-note source) to `docs/workshop/publish-ledger.jsonl`. After a first publish the
  tool also writes the item id into the `.vrgproj` `ModId` like the HUB does. Commit both.


**Why worktrees are refused.** A linked git worktree shares the project GUID with the canonical repo, and the
publish writes the Workshop item id into the `.vrgproj` and appends the ledger in the folder it ran from, so a publish
from a worktree would record them only on that worktree's branch; the next publish from the canonical folder would
not know the item. Merge (or fast-forward the canonical checkout), then publish from there.

## Linux specifics

* **Upload path: Python + ctypes over the Steamworks flat C API** (`libsteam_api.so`, interface `SteamUGC_v021`)
  talking to the **native Linux Steam client**. Why this and not the alternatives:
  * A C# console app on the game's `Steamworks.NET.dll` would load the Windows build of Steamworks.NET (it binds to
    `steam_api64.dll`); natively that needs DllImport remapping, under Proton it needs the shared prefix, its
    wineserver and Proton's lsteamclient bridge — the same prefix the SDK and the Proton game fight over.
  * The flat C API is Valve's stable ABI, there is nothing to compile, and the native client is already running.
  * No Keen or Valve binary is bundled; the redistributable is located at run time (Pulsar's copy).
  * The process identifies as app **1133870** through the `SteamAppId` environment variable (no `steam_appid.txt` is
    written anywhere). Steam shows you as "in game" while it runs, so close SE2 (native Pulsar or Proton) first; the
    tool refuses to start while it runs.
* **The SDK builder** runs under Proton with `WINEPREFIX=compatdata/1133870/pfx` (the prefix the SDK shares with
  the game), from the SDK `Editor/` folder, like `se2mod build`. By default it runs in a **sandbox**:
  an unprivileged user + mount namespace with an overlayfs over `steamapps/`, so the hash caches the builder writes
  into the SDK's `GameData/Vanilla|Engine`, and wine's writes to the prefix, land in the work dir and are thrown
  away. `--no-sandbox` runs it directly (it then updates those caches, like `se2mod build`). The sandbox needs
  `unshare` (util-linux) and unprivileged user namespaces; Kubuntu's AppArmor allows them for `unshare`.
  The overlay covers the Steam library holding the game's Proton prefix; with the Mod SDK in another library the
  tool refuses the sandbox and asks for `--no-sandbox`.
* `texconv.exe` is checked before and after the build: a hung one holds the shared prefix's wineserver (black
  gamescope window on the next Proton launch). The tool refuses to start if one is running and waits up to 30 s for it
  afterwards. The builder has a timeout (default 45 min) and the whole process group is killed on expiry.
* `--prepareForPublish` can log `Project validation failed` and still exit 0; the tool treats a missing stage as a
  failure and prints the builder's `fail:` lines.
* `--captureReferenceCache` (on, as in the HUB's default) writes `Assets/contentcache_reference.vrb(.meta)` into the
  *scratch* project. As the HUB does with the real project, a successful upload copies them back into the mod;
  commit them so the next publish ships them (the publish that captures them does not include them, same as the HUB).
* The builder needs the SDK's Vanilla/Engine projects and the game binaries; the tool builds its own symlink
  "standin" tree in the work dir (as `se2mod` does with its standin).

## First-publish checklist

1. Game and SDK on the same version; the mod builds and passes its in-game checklist natively.
2. `docs/workshop/description.bbcode` (≤ 7500 bytes), `CHANGELOG.md` section for the version, `thumb.jpg`
   (1280×720, < 1 MB, no Keen/SE2 logos), `publish.toml` (title ≤ 50, tags). For a scripted mod the description says
   scripts need `-loadScripts` (say whether the mod only works with scripts or has optional scripted extras).
3. Everything committed. For a mod built through modkit, `se2mod build-models <Mod> --release` on that commit; if it
   says it changed files, commit them and run it again until it stamps the build. Then
   `se2publish publish <Mod> --version X.Y.Z` (dry run) shows `READY FOR UPLOAD: yes`. Read the whole plan, the
   manifest and the change note.
4. **Accept the Steam Workshop legal agreement yourself** (it is account-wide; Steam hides items until you do). The
   tool never accepts anything; it only reports Steam's "needs to accept" flag.
5. Publish **Unlisted** (the default for a first upload). Close SE2 and the HUB first.
6. Commit the `.vrgproj` (now with `ModId`) and `docs/workshop/publish-ledger.jsonl`.
7. **Test with a second Steam account** (or a friend): subscribe to the unlisted item, unload/remove the local
   same-GUID project (`mods/<Mod>` must not be loaded at the same time), start a fresh world, test data-only and with
   `-loadScripts`.
8. Only then set visibility: `se2publish publish <Mod> --visibility Public …` on the next update, or on the item
   page.
9. Never publish this project from the Mod HUB again.

## When the game updates

Re-check, then bump the tool's references:

* What the HUB uploads: the call order, the KV tag, the metadata shape and member order, and the item id -> GUID
  rule.
* What the HUB stages, and the builder's options.
* `$Bundles` versions are read from the installed SDK/game DLLs at run time (no edit needed).
* Run `python3 -m pytest` — `test_writer_reproduces_the_games_own_files` re-checks the JSON writer against the
  game's own cached metadata files.
* Run `python -m pytest tests/publish` — `test_writer_reproduces_the_games_own_files` re-checks the JSON writer
  against the game's own cached metadata files.

No Keen code, decompiled sources or DLLs are in this repository. The publish code re-implements the observed
behavior of the game and SDK, and its tests check the JSON writer against the game's own cached metadata files.
