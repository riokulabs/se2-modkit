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

__all__ = ["BLENDER_PROCS", "Gpu", "GpuStats", "ProcGpu", "blender_processes", "describe", "least_busy",
           "parse_vulkan_names", "parse_vulkaninfo", "process_gpu", "read_stats", "sample", "supported",
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


def parse_vulkan_names(text: str) -> dict[int, str]:
    """{Vulkan index: device name} from `vulkaninfo` output, without the driver's note in brackets."""
    names: dict[int, str] = {}
    idx = None
    for line in text.splitlines():
        if m := re.match(r"^GPU(\d+):", line):
            idx = int(m.group(1))
        elif idx is not None and idx not in names and (m := re.search(r"deviceName\s*=\s*(.+)", line)):
            names[idx] = re.sub(r"\s*\(.*\)\s*$", "", m.group(1).strip())
    return names


def _vulkaninfo() -> str:
    if not supported():
        return ""
    try:
        return subprocess.run(["vulkaninfo"], capture_output=True, text=True, timeout=30, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def vulkan_devices(sysfs: Path = SYSFS) -> list[tuple[int, str]]:
    """[(Vulkan index, PCI address)] of the real GPUs; empty when `vulkaninfo` is missing or this is not Linux."""
    out = _vulkaninfo()
    return parse_vulkaninfo(out, sysfs) if out else []


def describe(sysfs: Path = SYSFS) -> list[tuple[int, str, str]]:
    """[(Vulkan index, PCI address, name)] of the real GPUs (one `vulkaninfo` call; cache the result)."""
    out = _vulkaninfo()
    names = parse_vulkan_names(out)
    return [(i, addr, names.get(i, "")) for i, addr in parse_vulkaninfo(out, sysfs)] if out else []


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


@dataclass(frozen=True)
class GpuStats:
    """One reading of a GPU's state, for display. Any value the driver doesn't report is None."""
    index: int
    addr: str
    name: str = ""
    busy: int | None = None          # percent, this instant
    vram_used: int | None = None     # bytes
    vram_total: int | None = None    # bytes
    temp: float | None = None        # degrees C at the edge sensor (or the first sensor)
    temp_hot: float | None = None    # degrees C at the hottest point (junction), where reported
    power: float | None = None       # watts
    power_cap: float | None = None   # watts
    fan: int | None = None           # rpm


def _hwmon(node: Path) -> Path | None:
    found = sorted(node.glob("hwmon/hwmon*"))
    return found[0] if found else None


def read_stats(devices: list[tuple[int, str, str]], sysfs: Path = SYSFS) -> list[GpuStats]:
    """A reading of every GPU in `devices` (from `describe`): load, VRAM, temperatures, power and fan."""
    out = []
    for index, addr, name in devices:
        node = sysfs / addr
        temps: dict[str, float] = {}
        temp = temp_hot = power = cap = fan = None
        if (mon := _hwmon(node)) is not None:
            for t in sorted(mon.glob("temp*_input")):
                raw = _read_int(t)
                if raw is None:
                    continue
                label_file = t.with_name(t.name.replace("_input", "_label"))
                label = label_file.read_text().strip() if label_file.exists() else t.name
                temps[label] = raw / 1000.0
            temp = temps.get("edge", next(iter(temps.values()), None))
            temp_hot = temps.get("junction")
            uw = _read_int(mon / "power1_average")
            uw = uw if uw is not None else _read_int(mon / "power1_input")
            power = None if uw is None else uw / 1e6
            cap_uw = _read_int(mon / "power1_cap")
            cap = None if cap_uw is None else cap_uw / 1e6
            fan = _read_int(mon / "fan1_input")
        out.append(GpuStats(index, addr, name, busy=_read_int(node / "gpu_busy_percent"),
                            vram_used=_read_int(node / "mem_info_vram_used"),
                            vram_total=_read_int(node / "mem_info_vram_total"),
                            temp=temp, temp_hot=temp_hot, power=power, power_cap=cap, fan=fan))
    return out


@dataclass
class ProcGpu:
    """What a set of processes holds on one GPU: VRAM in bytes and cumulative engine busy time (graphics plus
    compute) in nanoseconds."""
    vram: int = 0
    busy_ns: int = 0


_ENGINES = ("drm-engine-gfx", "drm-engine-compute")


def process_gpu(pids, proc: Path = Path("/proc")) -> dict[str, ProcGpu]:
    """{PCI address: ProcGpu} for the processes `pids`, from the kernel's per-client DRM accounting (fdinfo of each
    open GPU file; Linux 5.19+ with amdgpu, i915, xe, msm and others). A client opened through several files counts
    once."""
    out: dict[str, ProcGpu] = {}
    seen: set[tuple[str, str]] = set()
    for pid in pids:
        try:
            fds = list((proc / str(pid) / "fdinfo").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                text = fd.read_text()
            except OSError:
                continue
            if "drm-pdev" not in text:
                continue
            fields = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
            fields = {k.strip(): v.strip() for k, v in fields.items()}
            key = (fields.get("drm-pdev", ""), fields.get("drm-client-id", fd.name))
            if key in seen:
                continue
            seen.add(key)
            use = out.setdefault(key[0], ProcGpu())
            vram = fields.get("drm-memory-vram") or fields.get("drm-total-vram") or "0 KiB"
            use.vram += _bytes(vram)
            use.busy_ns += sum(int(fields.get(e, "0 ns").split()[0]) for e in _ENGINES if e in fields)
    return out


def _bytes(text: str) -> int:
    num, _, unit = text.partition(" ")
    scale = {"KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3}.get(unit.strip(), 1)
    try:
        return int(num) * scale
    except ValueError:
        return 0
