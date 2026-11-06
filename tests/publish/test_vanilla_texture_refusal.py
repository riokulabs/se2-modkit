"""A staged upload holding an exact copy of a game texture is refused."""

from types import SimpleNamespace

import pytest

from se2modkit.publish import cli as pcli
from se2modkit.publish.project import Refusal


def test_a_staged_game_texture_is_refused(tmp_path):
    game = tmp_path / "game"
    tex = game / "GameData" / "Vanilla" / "Content" / "BlockMaterials" / "Steel_cm.dds"
    tex.parent.mkdir(parents=True)
    tex.write_bytes(b"D" * 4096)
    stage = tmp_path / "stage" / "Content"
    stage.mkdir(parents=True)
    (stage / "Panel.dds").write_bytes(tex.read_bytes())
    env = SimpleNamespace(game_dir=game, sdk_dir=None, cache_dir=tmp_path / "cache")
    with pytest.raises(Refusal, match="Content/Panel.dds"):
        pcli._refuse_vanilla_textures(tmp_path / "stage", env)
    (stage / "Panel.dds").write_bytes(b"O" * 4096)
    pcli._refuse_vanilla_textures(tmp_path / "stage", env)  # its own texture passes
