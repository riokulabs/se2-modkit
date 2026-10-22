"""`se2mod gpu pick`: print the Vulkan device index of the least busy GPU (Blender `--gpu-device`)."""

from __future__ import annotations

import argparse
import sys

from .. import gpu
from . import register


def _add_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="gpu_cmd", metavar="SUBCOMMAND", required=True)
    pk = sub.add_parser("pick", help="print the least busy GPU's Vulkan device index (0 when nothing can be measured)",
                        description="Print the least busy GPU's Vulkan device index (0 when nothing can be measured).")
    pk.add_argument("--window", type=float, default=2.0, metavar="S",
                    help="seconds to average the load over (default 2)")
    pk.add_argument("--busy", action="store_true",
                    help="exit 1 without picking if a Blender process is already running")
    pk.add_argument("-v", "--verbose", action="store_true", help="print every GPU's load on stderr")


@register("gpu", "GPU helpers: gpu pick [--busy] [-v]", add_args=_add_args, check_game_version=False)
def cmd_gpu(args, env) -> int:
    if args.busy and gpu.blender_processes():
        print("a Blender job is already running; wait for it (one heavy GPU job at a time)", file=sys.stderr)
        return 1
    gpus = gpu.sample(window=max(args.window, 0.0))
    if args.verbose:
        if not gpus:
            print("no GPU load can be read here (Linux with vulkaninfo and a sysfs gpu_busy_percent only)",
                  file=sys.stderr)
        for g in gpus:
            print(f"device {g.index} ({g.addr}): {g.busy:.0f}% busy, {g.vram / 2**30:.1f} GiB VRAM", file=sys.stderr)
    pick = gpu.least_busy(gpus)
    print(0 if pick is None else pick)
    return 0
