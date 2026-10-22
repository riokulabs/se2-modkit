"""Helpers shared by the publish tests (a synthetic mod repo; nothing from Keen)."""
import json
import subprocess
from pathlib import Path

GUID = "11111111-2222-4333-8444-555555555555"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def vrgproj(name: str, guid: str = GUID, mod_id: str | None = None) -> str:
    pid = {"Guid": guid, "ModId": None, "ServiceId": ""}
    if mod_id:
        pid = {"Guid": guid, "ModId": {"Id": mod_id, "ServiceName": "Steam"}, "ServiceId": "Steam"}
    return json.dumps({"$Bundles": {"System.Runtime": "1.0.0.0", "VRage": "4.0.0.3151"},
                       "$Type": "VRage:Keen.VRage.Core.Project.VRageProjectData",
                       "$Value": {"Name": name, "Version": None, "Type": "Mod", "ProjectID": pid}}, indent=2) + "\n"


def make_jpeg(path: Path, size=(64, 36)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (30, 60, 90)).save(path, "JPEG")
