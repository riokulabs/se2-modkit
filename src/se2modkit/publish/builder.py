"""Run the SDK's headless builder (VRage.ContentPipeline.Builder.exe) under Proton's wine.

Two modes:
* shared  - the user's normal setup: WINEPREFIX = the SE2 prefix that the SDK shares (compatdata/1133870/pfx).
            The builder writes its usual caches (the prefix's AppData, the SDK GameData hash caches), exactly as
            `se2mod.py build` does.
* sandbox - the same command inside an unprivileged user+mount namespace with a copy-on-write overlay over the
            steamapps folder: every write the builder or wine makes under steamapps lands in the work dir, and the
            real Steam/SDK/game/prefix files are left untouched. Used for verification and for a clean run.

Builder options: -p <vrgproj>, --prepareForPublish <dir> (validate the project,
generate caches and copy it to the folder), --captureReferenceCache. These are the steps the HUB runs in-process.
"""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .hostenv import TEXCONV, BuildEnv, find_processes
from .project import Refusal
from .. import progress


def win_path(p: Path) -> str:
    return "Z:" + str(p).replace("/", "\\")


@dataclass
class BuildResult:
    returncode: int
    log: Path
    seconds: float
    commands: list[list[str]]


def check_texconv(when: str) -> None:
    hung = find_processes(TEXCONV)
    if hung:
        listing = "; ".join(f"pid {pid}: {cmd[:80]}" for pid, cmd in hung)
        raise Refusal(f"texconv is running {when} ({listing}). A hung texconv.exe holds the shared prefix's "
                      "wineserver. Stop it (kill the pid, then `wineserver -k` with WINEPREFIX set) and retry.")


def builder_commands(env: BuildEnv, project_file: Path, stage_dir: Path, prebuild: bool,
                     capture_reference: bool) -> list[list[str]]:
    wine = str(env.wine)
    exe = "VRage.ContentPipeline.Builder.exe"
    cmds = []
    if prebuild:
        cmds.append([wine, exe, "-p", win_path(project_file)])
    pub = [wine, exe, "-p", win_path(project_file), "--prepareForPublish", win_path(stage_dir)]
    if capture_reference:
        pub.append("--captureReferenceCache")
    cmds.append(pub)
    return cmds


def _sandbox_script(env: BuildEnv, cmds: list[list[str]], upper: Path, work: Path) -> list[str]:
    steamapps = env.steamapps.resolve()
    if not env.sdk_dir.resolve().is_relative_to(steamapps):
        raise Refusal(f"the sandbox overlays {steamapps} (the game's Proton prefix library), but the Mod SDK is in "
                      "another Steam library; run with --no-sandbox")
    for p in (steamapps, upper, work):
        if any(c in str(p) for c in ",:"):
            raise Refusal(f"overlay paths may not contain ',' or ':': {p}")
    inner = " && ".join(shlex.join(c) for c in cmds)
    inner_script = (f"cd {shlex.quote(str(env.sdk_editor))} && ( {inner} ); rc=$?; "
                    f"{shlex.quote(str(env.wineserver))} -k 2>/dev/null; exit $rc")
    outer = (f"mount -t overlay overlay -o lowerdir={steamapps},upperdir={upper},workdir={work} {steamapps} && "
             f"exec unshare -U --map-user={os.getuid()} --map-group={os.getgid()} -- bash -c {shlex.quote(inner_script)}")
    return ["unshare", "-Urm", "--", "bash", "-c", outer]


def run_builder(env: BuildEnv, project_file: Path, stage_dir: Path, log: Path, *, sandbox: bool,
                sandbox_dir: Path | None = None, prebuild: bool = True, capture_reference: bool = True,
                timeout: int = 2700) -> BuildResult:
    if not env.wine.exists():
        raise Refusal(f"Proton build not found: {env.wine} (set SE2_PROTON)")
    if not env.builder_exe.exists():
        raise Refusal(f"SDK builder not found: {env.builder_exe}")
    check_texconv("before the build")
    cmds = builder_commands(env, project_file, stage_dir, prebuild, capture_reference)
    proc_env = dict(os.environ, WINEPREFIX=str(env.prefix), WINEDEBUG="-all")
    if sandbox:
        assert sandbox_dir is not None
        upper, work = sandbox_dir / "upper", sandbox_dir / "work"
        for d in (upper, work):
            d.mkdir(parents=True, exist_ok=True)
        argv = _sandbox_script(env, cmds, upper, work)
        cwd = None
    else:
        argv = ["bash", "-c", " && ".join(shlex.join(c) for c in cmds)]
        cwd = env.sdk_editor
    log.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with log.open("wb") as lf:
        lf.write(("$ " + shlex.join(argv) + "\n").encode())
        lf.flush()
        p = subprocess.Popen(argv, cwd=cwd, env=proc_env, stdout=lf, stderr=subprocess.STDOUT,
                             start_new_session=True)
        beat = progress.Heartbeat("SDK builder", log)
        try:
            deadline = time.monotonic() + timeout
            while (rc := p.poll()) is None:
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(argv, timeout)
                beat.tick()
                time.sleep(1)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=30)
            if not sandbox:
                subprocess.run([str(env.wineserver), "-k"], env=proc_env, timeout=60, check=False)
            rc = 124
            lf.write(f"\n[se2publish] builder timed out after {timeout}s and was killed\n".encode())
    seconds = time.monotonic() - start
    # A texconv left behind would block the next build or the Proton game.
    deadline = time.monotonic() + 30
    while find_processes(TEXCONV) and time.monotonic() < deadline:
        time.sleep(2)
    check_texconv("after the build")
    return BuildResult(rc, log, seconds, cmds)


def clean_sandbox(sandbox_dir: Path) -> None:
    """Overlay work dirs can hold entries only the namespace's root may delete; remove them from inside one."""
    if not sandbox_dir.exists():
        return
    subprocess.run(["unshare", "-Ur", "rm", "-rf", "--", str(sandbox_dir)], timeout=600, check=False)
    if sandbox_dir.exists():
        shutil.rmtree(sandbox_dir, ignore_errors=True)
