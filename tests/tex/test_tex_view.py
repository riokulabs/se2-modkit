"""`tex.view`: the local page, its refusals of game textures, and the 127.0.0.1-only server. No network beyond a
loopback request to the test's own server."""

from __future__ import annotations

import http.client
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


def _get(port, path, host=None):
    """(status, body) of a GET to the test's own loopback server, with an optional Host header."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("GET", path, skip_host=host is not None)
        if host is not None:
            conn.putheader("Host", host)
        conn.endheaders()
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


@pytest.fixture
def served(tmp_path):
    out = tmp_path / "out"
    view.prepare(out, glb(tmp_path / "m.glb"), view.maps_in(_set(tmp_path)))
    (out / "secret.txt").write_text("not for the page")          # anything prepare() did not write
    (out / "maps" / "extra.png").write_bytes(b"x")
    httpd = view.server(out, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_binds_loopback_and_serves_the_page(tmp_path):
    view.prepare(tmp_path / "out", maps=view.maps_in(_set(tmp_path)))
    httpd = view.server(tmp_path / "out", 0)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=5) as r:
            assert b"three@0.160.0" in r.read()
        assert _get(port, "/")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_serves_only_the_generated_files(served):
    for path in ("/index.html", "/model.glb", "/maps/albedo.png", "/maps/normal.png", "/maps/roughness.png"):
        assert _get(served, path)[0] == 200, path
    for path in ("/secret.txt", "/maps/extra.png", "/maps/", "/maps", "/../m.glb", "/%2e%2e/m.glb",
                 "/maps/../secret.txt", "/maps/emissive.png", "//etc/passwd"):
        status, body = _get(served, path)
        assert status == 404 and b"not for the page" not in body, path


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}"])
def test_loopback_host_headers_are_served(served, host):
    assert _get(served, "/index.html", host.format(port=served))[0] == 200


@pytest.mark.parametrize("host", ["evil.example:{port}", "127.0.0.1", "localhost:1", "127.0.0.1:{port}.evil.example",
                                  ""])
def test_other_host_headers_are_refused(served, host):
    status, body = _get(served, "/index.html", host.format(port=served))
    assert status == 403 and b"three" not in body


def test_out_must_be_new_or_empty(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    view.prepare(out, maps=view.maps_in(_set(tmp_path)))            # an empty folder is fine
    with pytest.raises(view.ViewRefused, match="not empty"):
        view.prepare(out, maps=view.maps_in(_set(tmp_path)))
