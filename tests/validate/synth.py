"""Synthetic, hand-written inputs for the validators: a tiny decompiled tree, a facts table and a definition universe
(a server prefab, a client prefab, their compositions, one binding and one component definition). Nothing here is
copied from the game; the type and namespace names are made up."""

from __future__ import annotations

import json
from pathlib import Path

T_COMPOSITE = "VRage:Keen.VRage.DCS.Definitions.EntityCompositeDefinitionObjectBuilder"
T_PREFAB = "VRage:Keen.VRage.Core.Game.Definitions.PrefabDefinitionObjectBuilder"
T_BINDING = "VRage:Keen.VRage.Multiplayer.Data.PrefabBindingDefinitionObjectBuilder"
T_PLAIN = "Sim:Mod.Sim.PlainDefinitionObjectBuilder"

SERVER_TYPE = "Sim:Mod.Sim.ServerThingComponent"
CLIENT_TYPE = "Gfx.Client:Mod.Gfx.RenderThingComponent"

G = {k: f"00000000-0000-4000-8000-{i:012d}" for i, k in enumerate(
    ("server_comp", "server_prefab", "client_comp", "client_prefab", "binding", "plain", "k1", "k2", "k3"), 1)}


def facts(**extra) -> dict:
    """A facts table with two component types: one in a client assembly, one marked [ServerOnly]."""
    types = {
        "Mod.Gfx.RenderThingComponent": {"assembly": "Gfx.Client", "file": "Gfx.Client/Mod.Gfx/RenderThingComponent.cs",
                                         "decl": {"kind": "class", "line": 3}},
        "Mod.Sim.ServerThingComponent": {"assembly": "Sim", "file": "Sim/Mod.Sim/ServerThingComponent.cs",
                                         "decl": {"kind": "class", "line": 4, "server_only": True}},
    }
    types.update(extra)
    return {"format": 1, "game_version": "0.0.0.1", "source": "synthetic", "client_assemblies": ["Gfx.Client"],
            "migrations": {}, "types": types}


def schema_facts() -> dict:
    """facts() plus a definition class and its object builder with one required, one nullable and one value member."""
    return facts(**{
        "Mod.Sim.PlainDefinition": {"assembly": "Sim", "file": "Sim/Mod.Sim/PlainDefinition.cs",
                                    "decl": {"kind": "class", "line": 2, "ns": "Mod.Sim"}},
        "Mod.Sim.PlainDefinitionObjectBuilder": {
            "assembly": "Sim", "file": "Sim/Mod.Sim/PlainDefinitionObjectBuilder.cs",
            "decl": {"kind": "class", "line": 2, "ns": "Mod.Sim"},
            "fields": [["Name", "string", ""], ["Note", "string?", ""], ["Count", "int", ""], ["Label", "string", "set"]],
            "kinds": {"PlainDefinitionObjectBuilder": "class"}},
    })


def _composite(guid, *rows):
    return {"$Type": T_COMPOSITE, "$Value": {"Guid": guid, "TagSlots": [], "Components": [
        {"Key": k, "Value": {"Type": t, "Definition": d}} for k, t, d in rows]}}


def _prefab(guid, comp):
    return {"$Type": T_PREFAB, "$Value": {"Guid": guid, "_entity": {"Definition": comp, "ObjectBuilders": []}}}


def universe_files(*, server_types=(SERVER_TYPE,), client_types=(CLIENT_TYPE,)) -> dict[str, dict]:
    """rel path -> definition file of the synthetic load."""
    keys = iter((G["k1"], G["k2"], G["k3"]))
    return {
        "Mini/Block/Block_ServerComposition.def": _composite(G["server_comp"], *[(next(keys), t, None)
                                                                                 for t in server_types]),
        "Mini/Block/Block_ServerPrefab.def": _prefab(G["server_prefab"], G["server_comp"]),
        "Mini/Block/Block_ClientComposition.def": _composite(G["client_comp"], *[(f"{G['k3'][:-1]}{i}", t, None)
                                                                                 for i, t in enumerate(client_types)]),
        "Mini/Block/Block_ClientPrefab.def": _prefab(G["client_prefab"], G["client_comp"]),
        "Mini/Block/Block_Binding.def": {"$Type": T_BINDING, "$Value": {
            "Guid": G["binding"], "ServerComposite": G["server_comp"], "ClientPrefab": G["client_prefab"]}},
        "Mini/Block/Block_Plain.def": {"$Type": T_PLAIN, "$Value": {"Guid": G["plain"], "Name": "x", "Count": 1}},
    }


def write_tree(root: Path, files: dict[str, dict]) -> Path:
    for rel, d in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=1), encoding="utf-8", newline="\n")
    return root


# A tiny decompiled tree, in the <Assembly>/<Namespace>/<Type>.cs layout of `ilspycmd -p`.
DECOMPILE = {
    "Core/Keen.VRage.DCS.Components/Component.cs":
        "namespace Keen.VRage.DCS.Components;\n\npublic abstract class Component\n{\n}\n",
    "Core/Keen.VRage.Core.Game.RuntimeSystems.Components/ServerOnlyAttribute.cs":
        "namespace Keen.VRage.Core.Game.RuntimeSystems.Components;\n\npublic class ServerOnlyAttribute : Attribute\n"
        "{\n}\n",
    "Sim/Mod.Sim/ServerBaseComponent.cs":
        "using Keen.VRage.DCS.Components;\n\nnamespace Mod.Sim;\n\n[ServerOnly]\npublic abstract class "
        "ServerBaseComponent : Component\n{\n}\n",
    "Sim/Mod.Sim/ServerThingComponent.cs":
        "namespace Mod.Sim;\n\n[NameMigration(\"1.0\", \"OldThingComponent\", \"ServerThingComponent\")]\n"
        "public class ServerThingComponent : ServerBaseComponent\n{\n}\n",
    "Sim/Mod.Sim/PlainComponent.cs":
        "using Keen.VRage.DCS.Components;\n\nnamespace Mod.Sim;\n\npublic class PlainComponent : Component\n{\n"
        "\t[Definition]\n\tpublic PlainDefinition Definition;\n}\n",
    "Sim/Mod.Sim/PlainDefinition.cs":
        "namespace Mod.Sim;\n\npublic class PlainDefinition\n{\n}\n",
    "Sim/Mod.Sim/PlainDefinitionObjectBuilder.cs":
        "namespace Mod.Sim;\n\npublic class PlainDefinitionObjectBuilder\n{\n\tpublic string Name;\n\n"
        "\tpublic string? Note;\n\n\tpublic int Count;\n\n\tpublic string Label = \"x\";\n\n"
        "\tpublic string Empty = null;\n\n\tpublic Inner Nested;\n\n\tpublic struct Inner\n\t{\n\t}\n}\n",
    "Gfx.Client/Mod.Gfx/RenderThingComponent.cs":
        "using Keen.VRage.DCS.Components;\n\nnamespace Mod.Gfx;\n\npublic class RenderThingComponent : Component\n{\n}\n",
    "Tools/Mod.Tools/ToolComponent.cs":
        "using Keen.VRage.DCS.Components;\n\nnamespace Mod.Tools;\n\npublic class ToolComponent : Component\n{\n}\n",
    "Tools/Properties/AssemblyInfo.cs": "[assembly: ClientAssembly]\n",
}


def write_decompile(root: Path) -> Path:
    for rel, text in DECOMPILE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    return root
