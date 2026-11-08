"""`tex.view`: the local page, its refusals of game textures, and the 127.0.0.1-only server. No network beyond a
loopback request to the test's own server."""

from __future__ import annotations

import json
import struct
import threading
import urllib.request

import pytest

from se2modkit.tex import png, view


def glb(path, images=()):
    """A minimal binary glTF whose BIN chunk holds `images` (bytes) as buffer views."""
    binary, views = b"", []
    for b in images:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(b)})
        binary += b + b"\x00" * (-len(b) % 4)
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binary)}], "bufferViews": views,
           "images": [{"bufferView": i, "mimeType": "image/png"} for i in range(len(views))]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
    return path


def _set(tmp_path):
    d = tmp_path / "Hull"
    png.flat(d / "Hull_albedo.png", 8, (128, 128, 128))
    png.flat(d / "Hull_normal.png", 8, (128, 128, 255))
    png.flat(d / "Hull_roughness.png", 8, 128)
    (d / "Hull_notes.png").write_bytes(b"not a role")
    return d


def test_maps_in_picks_roles_by_name(tmp_path):
    assert sorted(view.maps_in(_set(tmp_path))) == ["albedo", "normal", "roughness"]


def test_prepare_writes_a_pinned_local_page(tmp_path):
    out = tmp_path / "out"
    page = view.prepare(out, glb(tmp_path / "m.glb"), view.maps_in(_set(tmp_path)))
    html = page.read_text(encoding="utf-8")
    assert "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js" in html
    assert "Local-only preview" in html and "__CONFIG__" not in html
    assert (out / "model.glb").is_file() and (out / "maps" / "normal.png").is_file()
    assert '"normal": "maps/normal.png"' in html


def test_nothing_to_show_is_refused(tmp_path):
    with pytest.raises(view.ViewRefused, match="Nothing to show"):
        view.prepare(tmp_path / "o")


def test_a_copied_game_texture_is_refused(tmp_path):
    vanilla = tmp_path / "game" / "GameData" / "Vanilla"
    tex = vanilla / "Content" / "Steel_albedo.png"
    tex.parent.mkdir(parents=True)
    tex.write_bytes(b"V" * 4096)
    d = _set(tmp_path)
    (d / "Hull_albedo.png").write_bytes(tex.read_bytes())
    with pytest.raises(view.ViewRefused, match="exact copy of the game texture Content/Steel_albedo.png"):
        view.prepare(tmp_path / "o", maps=view.maps_in(d), vanilla_roots=[vanilla])
    with pytest.raises(view.ViewRefused, match="embedded in the model"):
        view.prepare(tmp_path / "o", glb(tmp_path / "m.glb", [tex.read_bytes()]), vanilla_roots=[vanilla])
    with pytest.raises(view.ViewRefused, match="inside the game or Mod SDK folder"):
        view.prepare(tmp_path / "o", maps={"albedo": tex}, vanilla_roots=[vanilla])
    assert not (tmp_path / "o" / "index.html").exists()


def test_not_a_glb(tmp_path):
    (tmp_path / "m.glb").write_bytes(b"nope")
    with pytest.raises(view.ViewRefused, match="not a binary glTF"):
        view.prepare(tmp_path / "o", tmp_path / "m.glb")


def test_server_binds_loopback_and_serves_the_page(tmp_path):
    view.prepare(tmp_path, maps=view.maps_in(_set(tmp_path)))
    httpd = view.server(tmp_path, 0)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/index.html", timeout=5) as r:
            assert b"three@0.160.0" in r.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
