"""`se2mod lock take|release|list`: advisory leases on shared files, such as a .blend two sessions both edit, and on the
live Blender (`live-blender`)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .. import lease
from . import register


def _owner(args) -> str:
    return lease.holder_name(args.owner)


def _add_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="lock_cmd", metavar="SUBCOMMAND", required=True)
    what = (f"a file, or {lease.LIVE_BLENDER}: {lease.NAMED[lease.LIVE_BLENDER]}. Take it before you drive that "
            "Blender, so another session doesn't drive it at the same time")
    t = sub.add_parser("take", help="lease a file (or the live Blender) to you; renews your own lease",
                       description=f"Lease PATH to you. PATH is {what}.")
    t.add_argument("path", type=Path, metavar="PATH", help=what)
    t.add_argument("--owner", help="who holds it (default: $SE2_SESSION, else your user name)")
    t.add_argument("--ttl", default="4h", help="how long the lease lasts: seconds, or 30m, 4h (default 4h)")
    r = sub.add_parser("release", help="drop your lease on a file (or on live-blender)")
    r.add_argument("path", type=Path, metavar="PATH", help=f"a file, or {lease.LIVE_BLENDER}")
    r.add_argument("--owner", help="who holds it (default: $SE2_SESSION, else your user name)")
    r.add_argument("--force", action="store_true", help="drop someone else's lease")
    sub.add_parser("list", help="every lease still in force")


@register("lock", "Advisory leases on shared files and the live Blender: lock take|release|list", add_args=_add_args,
          check_game_version=False)
def cmd_lock(args, env) -> int:
    try:
        if args.lock_cmd == "take":
            got = lease.take(env.cache_dir, args.path, owner=_owner(args), ttl=lease.parse_ttl(args.ttl))
            print(f"leased {got.path} to {got.owner} for {got.ttl / 3600:g} h")
        elif args.lock_cmd == "release":
            print("released" if lease.release(env.cache_dir, args.path, owner=_owner(args), force=args.force)
                  else f"no lease on {args.path}")
        else:
            now = time.time()
            for x in lease.active(env.cache_dir, now=now):
                left = (x.since + x.ttl - now) / 60
                print(f"{x.owner}\t{x.path}\t{left:.0f} min left")
    except lease.LeaseHeld as e:
        left = (e.lease.since + e.lease.ttl - time.time()) / 60
        print(f"{e.lease.path} is held by {e.lease.owner} for {left:.0f} more min; ask them, wait, or drop their "
              f"lease with `se2mod lock release --force`.", file=sys.stderr)
        return 1
    except lease.LeaseBusy as e:
        print(str(e), file=sys.stderr)
        return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0
