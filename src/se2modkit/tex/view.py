"""`se2mod tex view`: a local three.js page that shows a GLB, or a sphere and a cube, wearing a mod's own maps under
adjustable light. The page is written to a preview folder and served on 127.0.0.1 only; it is a local look-dev aid,
never something to publish.

  maps_in(folder)                          {role: PNG} of a texture set folder, by file name (`<set>_<role>.png`)
  prepare(out, model=None, maps=None, *,   copy the model and maps into `out` and write `index.html`; refuses
          vanilla_roots=(), forbidden=())  (ViewRefused) a file under a game folder or any copy of a game texture,
                                           embedded GLB images included
  serve(folder, port=0)                    serve the folder on 127.0.0.1 until interrupted

three.js (MIT) is loaded from a pinned version on the jsDelivr CDN.
"""

from __future__ import annotations

import functools
import json
import shutil
import struct
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..validate import vanilla_textures

__all__ = ["LOCAL_ONLY", "THREE_VERSION", "ViewRefused", "glb_images", "maps_in", "prepare", "serve"]

THREE_VERSION = "0.160.0"
THREE_CDN = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}"
ROLES = ("albedo", "normal", "roughness", "metal", "ao", "emissive", "colormask")
LOCAL_ONLY = ("Local-only preview: served on 127.0.0.1 for your own look-dev. Never publish this page or point it "
              "at game textures.")


class ViewRefused(ValueError):
    """The viewer will not show these files; the message says why."""


def maps_in(folder: Path) -> dict[str, Path]:
    """{role: PNG} of a set folder: `<anything>_<role>.png` for each known role (the first match wins)."""
    out: dict[str, Path] = {}
    for f in sorted(Path(folder).glob("*.png")):
        role = f.stem.rsplit("_", 1)[-1].lower()
        if role in ROLES:
            out.setdefault(role, f)
    return out


def glb_images(path: Path) -> list[bytes]:
    """The image bytes embedded in a binary glTF (images stored in buffer views of its BIN chunk)."""
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ViewRefused(f"{path} is not a binary glTF (.glb) file.")
    pos, doc, binary = 12, None, b""
    while pos + 8 <= len(data):
        n, kind = struct.unpack_from("<I4s", data, pos)
        chunk = data[pos + 8:pos + 8 + n]
        if kind == b"JSON":
            doc = json.loads(chunk.decode("utf-8"))
        elif kind == b"BIN\x00":
            binary = chunk
        pos += 8 + n
    out = []
    for img in (doc or {}).get("images") or []:
        bv = img.get("bufferView")
        if bv is None:
            continue
        v = doc["bufferViews"][bv]
        start = v.get("byteOffset", 0)
        out.append(binary[start:start + v["byteLength"]])
    return out


def _inside(p: Path, roots) -> bool:
    p = Path(p).resolve()
    for r in roots:
        try:
            p.relative_to(Path(r).resolve())
            return True
        except ValueError:
            continue
    return False


def _guard(files: list[Path], embedded: list[bytes], vanilla_roots, forbidden) -> None:
    under = [f for f in files if _inside(f, [*vanilla_roots, *forbidden])]
    if under:
        raise ViewRefused(f"{under[0]} is inside the game or Mod SDK folder; the viewer shows only your own files.")
    with tempfile.TemporaryDirectory(prefix="se2tex-glb-") as tmp:
        dumped = []
        for i, b in enumerate(embedded):
            p = Path(tmp, f"embedded{i}.img")
            p.write_bytes(b)
            dumped.append(p)
        copies = vanilla_textures.find_copies([*files, *dumped], list(vanilla_roots))
        if copies:
            f, v = copies[0]
            what = "an image embedded in the model" if f.parent == Path(tmp) else str(f)
            raise ViewRefused(f"{what} is an exact copy of the game texture {v.parent.name}/{v.name}; the viewer "
                              "shows only your own textures.")


def prepare(out: Path, model: Path | None = None, maps: dict[str, Path] | None = None, *, vanilla_roots=(),
            forbidden=()) -> Path:
    """Write the viewer into `out` (made if missing) and return its index.html."""
    maps = dict(maps or {})
    if model is None and not maps:
        raise ViewRefused("Nothing to show: give a .glb model or a texture set folder with <set>_<role>.png maps.")
    files = [*maps.values(), *([model] if model else [])]
    for f in files:
        if not Path(f).is_file():
            raise ViewRefused(f"{f} does not exist.")
    _guard([Path(f) for f in files], glb_images(model) if model else [], vanilla_roots, forbidden)
    out = Path(out)
    (out / "maps").mkdir(parents=True, exist_ok=True)
    cfg = {"model": None, "maps": {}}
    if model is not None:
        shutil.copyfile(model, out / "model.glb")
        cfg["model"] = "model.glb"
    for role, f in maps.items():
        shutil.copyfile(f, out / "maps" / f"{role}.png")
        cfg["maps"][role] = f"maps/{role}.png"
    page = out / "index.html"
    page.write_text(PAGE.replace("__THREE__", THREE_CDN).replace("__CONFIG__", json.dumps(cfg))
                    .replace("__NOTE__", LOCAL_ONLY), encoding="utf-8")
    return page


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):   # no request log on the terminal
        pass


def server(folder: Path, port: int = 0) -> ThreadingHTTPServer:
    """An HTTP server for `folder` bound to 127.0.0.1 (port 0 picks a free one); call serve_forever() on it."""
    return ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_Quiet, directory=str(folder)))


def serve(folder: Path, port: int = 0, *, announce=print) -> None:
    httpd = server(folder, port)
    announce(f"http://127.0.0.1:{httpd.server_address[1]}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Texture Viewer</title>
<style>
  :root { color-scheme: dark; --bg: #15171a; --panel: #1f2226; --text: #d8dbe0; --muted: #8b9098; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text); font: 13px system-ui, sans-serif; }
  #view { position: fixed; inset: 0; }
  #ui { position: fixed; top: 12px; left: 12px; width: 240px; background: var(--panel); padding: 10px 12px;
        border-radius: 6px; max-height: calc(100% - 48px); overflow: auto; }
  #ui label { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin: 6px 0; }
  #ui input[type=range] { width: 120px; }
  #note { position: fixed; bottom: 8px; left: 12px; right: 12px; color: var(--muted); font-size: 12px; }
  h1 { font-size: 13px; margin: 0 0 6px; }
</style>
<script type="importmap">
{ "imports": { "three": "__THREE__/build/three.module.js", "three/addons/": "__THREE__/examples/jsm/" } }
</script>
</head>
<body>
<div id="view"></div>
<div id="ui">
  <h1>Texture viewer</h1>
  <label>Shape <select id="shape"></select></label>
  <label>Key light <input id="key" type="range" min="0" max="8" step="0.1" value="3"></label>
  <label>Light angle <input id="az" type="range" min="-180" max="180" step="1" value="35"></label>
  <label>Light height <input id="el" type="range" min="-10" max="90" step="1" value="40"></label>
  <label>Fill <input id="fill" type="range" min="0" max="3" step="0.05" value="0.4"></label>
  <label>Reflections <input id="envi" type="range" min="0" max="2" step="0.05" value="0.6"></label>
  <label>Exposure <input id="exp" type="range" min="0.2" max="3" step="0.05" value="1"></label>
  <label>Normal strength <input id="nrm" type="range" min="0" max="3" step="0.05" value="1"></label>
  <label>Glow <input id="glow" type="range" min="0" max="20" step="0.1" value="3"></label>
  <label>Paint <input id="paintOn" type="checkbox" checked> <input id="paint" type="color" value="#6b7078"></label>
  <div id="maps" style="color: var(--muted)"></div>
</div>
<div id="note">__NOTE__</div>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const CONFIG = __CONFIG__;
const $ = (id) => document.getElementById(id);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(devicePixelRatio);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
$("view").appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x15171a);
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1000);
camera.position.set(3.2, 2.0, 3.8);
const controls = new OrbitControls(camera, renderer.domElement);
const key = new THREE.DirectionalLight(0xffffff, 3);
const fill = new THREE.HemisphereLight(0xdde6ff, 0x302820, 0.4);
scene.add(key, fill);

const loader = new THREE.TextureLoader();
const tex = {};
for (const [role, url] of Object.entries(CONFIG.maps)) {
  const t = loader.load(url);
  t.colorSpace = (role === "albedo") ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  tex[role] = t;
}
$("maps").textContent = "Maps: " + (Object.keys(tex).join(", ") || "none (the model's own)");

const paint = { color: { value: new THREE.Color() }, on: { value: 1 }, mask: { value: tex.colormask || null } };
function material(flipY) {
  for (const t of Object.values(tex)) { if (t.flipY !== flipY) { t.flipY = flipY; t.needsUpdate = true; } }
  const m = new THREE.MeshStandardMaterial({
    color: 0xffffff, map: tex.albedo || null, normalMap: tex.normal || null,
    roughness: 1, roughnessMap: tex.roughness || null, metalness: tex.metal ? 1 : 0, metalnessMap: tex.metal || null,
    aoMap: tex.ao || null, emissive: tex.emissive ? 0xffffff : 0x000000, emissiveMap: tex.emissive || null,
  });
  if (!tex.roughness) m.roughness = 0.5;
  if (tex.colormask && tex.albedo) {
    m.onBeforeCompile = (s) => {
      s.uniforms.paintColor = paint.color; s.uniforms.paintOn = paint.on; s.uniforms.colorMaskMap = paint.mask;
      s.fragmentShader = "uniform vec3 paintColor; uniform float paintOn; uniform sampler2D colorMaskMap;\n" +
        s.fragmentShader.replace("#include <map_fragment>", `#include <map_fragment>
        float cmask = texture2D(colorMaskMap, vMapUv).r * paintOn;
        diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * paintColor * 2.0, cmask);`);
    };
  }
  return m;
}

const shapes = {};
shapes.sphere = new THREE.Mesh(new THREE.SphereGeometry(1, 128, 64), material(true));
shapes.cube = new THREE.Mesh(new THREE.BoxGeometry(1.5, 1.5, 1.5), material(true));
for (const name of Object.keys(shapes)) $("shape").add(new Option(name, name));
if (CONFIG.model) {
  new GLTFLoader().load(CONFIG.model, (g) => {
    const root = g.scene;
    if (Object.keys(tex).length) {
      const m = material(false);   // glTF UVs: no vertical flip
      root.traverse((o) => { if (o.isMesh) o.material = m; });
    }
    const box = new THREE.Box3().setFromObject(root), size = box.getSize(new THREE.Vector3()).length() || 1;
    root.position.sub(box.getCenter(new THREE.Vector3()));
    root.scale.setScalar(2.5 / size);
    shapes.model = root;
    $("shape").add(new Option("model", "model"));
    $("shape").value = "model";
    show();
  });
}
let current = null;
function show() {
  if (current) scene.remove(current);
  current = shapes[$("shape").value];
  if (current) scene.add(current);
}
function update() {
  const az = THREE.MathUtils.degToRad(+$("az").value), el = THREE.MathUtils.degToRad(+$("el").value);
  key.intensity = +$("key").value;
  key.position.set(Math.cos(el) * Math.sin(az), Math.sin(el), Math.cos(el) * Math.cos(az)).multiplyScalar(5);
  fill.intensity = +$("fill").value;
  renderer.toneMappingExposure = +$("exp").value;
  paint.color.value.set($("paint").value);
  paint.on.value = $("paintOn").checked ? 1 : 0;
  const all = new Set([shapes.sphere.material, shapes.cube.material]);
  if (shapes.model) shapes.model.traverse((o) => { if (o.isMesh) all.add(o.material); });
  for (const m of all) {
    if (m.envMapIntensity !== undefined) m.envMapIntensity = +$("envi").value;
    if (m.normalScale) m.normalScale.setScalar(+$("nrm").value);
    if (m.emissiveIntensity !== undefined) m.emissiveIntensity = +$("glow").value;
  }
}
for (const el of document.querySelectorAll("#ui input")) el.addEventListener("input", update);
$("shape").addEventListener("change", show);
function resize() {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize(); show(); update();
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
</script>
</body>
</html>
"""
