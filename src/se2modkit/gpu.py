"""Which GPU is least busy, as a Blender `--gpu-device` (Vulkan) index.

    blender -b --gpu-backend vulkan --gpu-device "$(se2mod gpu pick)" ...

Vulkan device indices are matched to Linux GPUs through their PCI bus (`vulkaninfo`), and each GPU's load is read from
sysfs (`gpu_busy_percent`, `mem_info_vram_used`), averaged over a short window. Ties go to the GPU with less VRAM in
use. Software devices (llvmpipe) never have a PCI bus and are never picked.

Sampling works on Linux only (the sysfs files are written by the amdgpu driver; other drivers may not provide them).
Elsewhere, or when nothing can be measured, there are no GPUs to choose from and callers fall back to device 0.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .env import procs

__all__ = ["BLENDER_PROCS", "Gpu", "blender_processes", "least_busy", "parse_vulkaninfo", "sample", "supported",
           "vulkan_devices"]

SYSFS = Path("/sys/bus/pci/devices")
BLENDER_PROCS = ("blender", "blender.exe")


@dataclass(frozen=True)
class Gpu:
    index: int            # Vulkan device index (Blender --gpu-device)
    addr: str = ""        # PCI address, e.g. 0000:03:00.0
    busy: float = 0.0     # average busy percent over the window
    vram: int = 0         # VRAM bytes in use


def supported(platform: str = sys.platform) -> bool:
    """GPU load can be sampled on this platform."""
    return platform.startswith("linux")


def parse_vulkaninfo(text: str, sysfs: Path = SYSFS) -> list[tuple[int, str]]:
    """[(Vulkan index, PCI address)] for GPUs whose sysfs node reports a load, in Vulkan enumeration order."""
    devs: list[tuple[int, str]] = []
    idx = dom = bus = dev = None
    for line in text.splitlines():
        if m := re.match(r"^GPU(\d+):", line):
            idx, dom, bus, dev = int(m.group(1)), None, None, None
        elif idx is not None and (m := re.search(r"pci(Domain|Bus|Device)\s*=\s*(\d+)", line)):
            val = int(m.group(2))
            if m.group(1) == "Domain":
                dom = val
            elif m.group(1) == "Bus":
                bus = val
            else:
                dev = val
            if None not in (dom, bus, dev):
                addr = f"{dom:04x}:{bus:02x}:{dev:02x}.0"
                if (sysfs / addr / "gpu_busy_percent").exists() and (idx, addr) not in devs:
                    devs.append((idx, addr))
                idx = None
    return devs


def vulkan_devices(sysfs: Path = SYSFS) -> list[tuple[int, str]]:
    """[(Vulkan index, PCI address)] of the real GPUs; empty when `vulkaninfo` is missing or this is not Linux."""
    if not supported():
        return []
    try:
        out = subprocess.run(["vulkaninfo"], capture_output=True, text=True, timeout=30, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_vulkaninfo(out, sysfs)


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def sample(devices: list[tuple[int, str]] | None = None, window: float = 2.0, sysfs: Path = SYSFS) -> list[Gpu]:
    """Each GPU's busy percent averaged over `window` seconds, and its VRAM in use."""
    devices = vulkan_devices(sysfs) if devices is None else devices
    if not devices:
        return []
    samples: dict[int, list[int]] = {i: [] for i, _ in devices}
    end = time.monotonic() + window
    while True:
        for i, addr in devices:
            v = _read_int(sysfs / addr / "gpu_busy_percent")
            if v is not None:
                samples[i].append(v)
        if time.monotonic() >= end:
            break
        time.sleep(0.25)
    out = []
    for i, addr in devices:
        if samples[i]:
            out.append(Gpu(i, addr, sum(samples[i]) / len(samples[i]), _read_int(sysfs / addr / "mem_info_vram_used") or 0))
    return out


def least_busy(gpus: list[Gpu]) -> int | None:
    """The index of the least busy GPU (ties: less VRAM in use, then the lower index); None for no GPUs."""
    if not gpus:
        return None
    return min(gpus, key=lambda g: (g.busy, g.vram, g.index)).index


def blender_processes() -> list[tuple[int, str]]:
    """(pid, command line) of every running Blender."""
    return procs.find_processes(BLENDER_PROCS)
