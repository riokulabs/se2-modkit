"""GPU picking from a fake vulkaninfo and a fake sysfs tree; no GPU is touched."""

from __future__ import annotations

import os

import pytest

from se2modkit import gpu

sysfs_names = pytest.mark.skipif(os.name == "nt", reason="sysfs PCI names contain ':', not valid in Windows paths")

VULKANINFO = """\
GPU0:
    VkPhysicalDevicePCIBusInfoPropertiesEXT:
        pciDomain   = 0
        pciBus      = 68
        pciDevice   = 0
GPU1:
    VkPhysicalDevicePCIBusInfoPropertiesEXT:
        pciDomain   = 0
        pciBus      = 9
        pciDevice   = 0
GPU2:
    deviceName = llvmpipe
"""


def _sysfs(tmp_path, cards):
    for addr, (busy, vram) in cards.items():
        d = tmp_path / addr
        d.mkdir(parents=True)
        (d / "gpu_busy_percent").write_text(f"{busy}\n")
        (d / "mem_info_vram_used").write_text(f"{vram}\n")
    return tmp_path


@sysfs_names
def test_vulkan_indices_map_to_pci_addresses(tmp_path):
    sysfs = _sysfs(tmp_path, {"0000:44:00.0": (0, 0), "0000:09:00.0": (0, 0)})
    assert gpu.parse_vulkaninfo(VULKANINFO, sysfs) == [(0, "0000:44:00.0"), (1, "0000:09:00.0")]
    assert gpu.parse_vulkaninfo(VULKANINFO, tmp_path / "none") == []


@sysfs_names
def test_least_busy_then_less_vram(tmp_path):
    sysfs = _sysfs(tmp_path, {"0000:44:00.0": (70, 1), "0000:09:00.0": (5, 2)})
    gpus = gpu.sample([(0, "0000:44:00.0"), (1, "0000:09:00.0")], window=0, sysfs=sysfs)
    assert [(g.index, g.busy, g.vram) for g in gpus] == [(0, 70, 1), (1, 5, 2)]
    assert gpu.least_busy(gpus) == 1
    assert gpu.least_busy([gpu.Gpu(0, busy=5, vram=9), gpu.Gpu(1, busy=5, vram=3)]) == 1
    assert gpu.least_busy([]) is None


def test_gpu_pick_command(run, monkeypatch):
    monkeypatch.setattr(gpu, "blender_processes", lambda: [])
    monkeypatch.setattr(gpu, "sample", lambda window=2.0: [gpu.Gpu(0, "a", 80, 0), gpu.Gpu(1, "b", 3, 0)])
    code, out, err = run("gpu", "pick", "-v")
    assert code == 0 and out.strip() == "1" and "device 1 (b): 3% busy" in err


def test_gpu_pick_without_gpus_prints_zero_and_busy_refuses(run, monkeypatch):
    monkeypatch.setattr(gpu, "sample", lambda window=2.0: [])
    monkeypatch.setattr(gpu, "blender_processes", lambda: [])
    assert run("gpu", "pick")[:2] == (0, "0\n")
    monkeypatch.setattr(gpu, "blender_processes", lambda: [(1, "blender -b")])
    code, out, err = run("gpu", "pick", "--busy")
    assert code == 1 and out == "" and "already running" in err


def test_vulkan_names_drop_the_driver_note():
    text = VULKANINFO.replace("GPU0:\n", "GPU0:\n    deviceName        = AMD Radeon RX 7900 XT (RADV NAVI31)\n")
    assert gpu.parse_vulkan_names(text) == {0: "AMD Radeon RX 7900 XT", 2: "llvmpipe"}


@sysfs_names
def test_read_stats_reads_load_vram_and_hwmon(tmp_path):
    sysfs = _sysfs(tmp_path, {"0000:44:00.0": (37, 2 * 1024 ** 3), "0000:09:00.0": (0, 0)})
    node = sysfs / "0000:44:00.0"
    (node / "mem_info_vram_total").write_text(f"{20 * 1024 ** 3}\n")
    mon = node / "hwmon" / "hwmon3"
    mon.mkdir(parents=True)
    for name, value in {"temp1_input": 62000, "temp1_label": "edge", "temp2_input": 72000, "temp2_label": "junction",
                        "power1_average": 69000000, "power1_cap": 282000000, "fan1_input": 583}.items():
        (mon / name).write_text(f"{value}\n")
    hot, idle = gpu.read_stats([(0, "0000:44:00.0", "RX"), (1, "0000:09:00.0", "")], sysfs)
    assert (hot.busy, hot.vram_used, hot.vram_total) == (37, 2 * 1024 ** 3, 20 * 1024 ** 3)
    assert (hot.temp, hot.temp_hot, hot.power, hot.power_cap, hot.fan) == (62.0, 72.0, 69.0, 282.0, 583)
    assert (idle.busy, idle.vram_total, idle.temp, idle.power) == (0, None, None, None)   # unreported stays None
