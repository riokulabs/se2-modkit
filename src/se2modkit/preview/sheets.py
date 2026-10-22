"""Sheets of a preview set (Pillow, imported lazily; never imported by Blender code).

  contact(paths, out)        a labelled contact sheet of a set's renders (`compose.make_sheet`)
  states_sheet(set_dir, ...) the states set (`preview.states`): per block its vanilla analogue rows on top, then per
                             view one row of every shot, a before set's row above when given, the per-shot pixel
                             difference to the before row (% of pixels whose channels differ by > DIFF_LEVEL / 255)
                             and the FR LOD0 share of the scaffold / support / hide / stand-in triangles
  icon_sheet(ours, vanilla)  icons beside vanilla icons on the dark G-screen tone and a light tone at 200 px, then at
                             the in-game 128 px and 64 px, and an optional G-screen tile mock-up
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

from .compose import PreviewError, _font, _import_pillow, make_sheet

__all__ = ["DIFF_LEVEL", "contact", "diff_pct", "icon_sheet", "states_sheet"]

TW, TH = 216, 162                      # states thumbnail
LABEL_W, SIDE_W, HEAD_H, ROW_PAD = 230, 250, 58, 22
BG, INK, FAINT = (247, 247, 244), (40, 42, 46), (150, 152, 156)
BEFORE_C, AFTER_C, ANA_C, HOT = (110, 112, 118), (25, 90, 180), (120, 70, 20), (200, 40, 30)
SHARE_KEYS = ("scaffold", "support", "hide", "standin")
DIFF_LEVEL = 8
SHOTS = ("c00", "c10", "c20", "c30", "c53", "c77", "done", "done_damaged", "one_hit", "battered", "broken")
DEBRIS = "debris"
VIEWS = ("front", "rear")
DARK, LIGHT = (38, 43, 51, 255), (196, 200, 206, 255)


def contact(paths: Sequence[str | Path], out: str | Path, *, cols: int = 5, title: str | None = None,
            thumb: int = 360) -> tuple[int, int]:
    """A labelled contact sheet of `paths` (file names as labels)."""
    return make_sheet([str(p) for p in paths], Path(out), cols=cols, labels=True, title=title, thumb=thumb)


def diff_pct(a, b) -> float | None:
    """% of pixels whose largest channel difference exceeds DIFF_LEVEL (None when either image is missing or the
    sizes differ)."""
    if a is None or b is None or a.size != b.size:
        return None
    import numpy as np
    x = np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).max(axis=2)
    return 100.0 * float((x > DIFF_LEVEL).mean())


# ---------------------------------------------------------------------------------------------------- states
def _png(Image, d, key, shot, view):
    p = Path(d) / key / f"{shot}_{view}.png"
    if not p.exists():
        return None
    with Image.open(p) as im:
        return im.convert("RGB")


def _shares(d, key) -> dict:
    p = Path(d) / key / "info.json"
    if not p.exists():
        return {}
    info = json.loads(p.read_text(encoding="utf-8"))
    tot, rt = info.get("fr_lod0_tris"), info.get("role_tris") or {}
    return {k: rt[k] / tot for k in SHARE_KEYS if k in rt and tot}


def _rows(key, after, before, analogue=False):
    out = []
    for view in VIEWS:
        if analogue:
            out.append((f"vanilla  {view}", ANA_C, after, key, view, None))
            continue
        if before:
            out.append((f"before  {view}", BEFORE_C, before, key, view, None))
        out.append((f"after  {view}" if before else view, AFTER_C, after, key, view, before))
    return out


def _draw(title, groups, path):
    Image, _, ImageDraw, ImageFont = _import_pillow()

    def font(px, bold=False):
        return _font(ImageFont, px)
    shots = SHOTS
    if any(_png(Image, d, key, DEBRIS, view) for _, rows, _ in groups for _, _, d, key, view, _ in rows):
        shots += (DEBRIS,)
    n = len(shots)
    W = LABEL_W + n * TW + SIDE_W
    H = HEAD_H + sum(26 + len(r) * (TH + ROW_PAD) + 10 for _, r, _ in groups) + 10
    im = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(im)
    dr.text((10, 8), title, fill=INK, font=font(18))
    for i, s in enumerate(shots):
        dr.text((LABEL_W + i * TW + 6, 36), s, fill=INK, font=font(14))
    dr.text((LABEL_W + n * TW + 8, 36), "diff % / FR LOD0 share", fill=INK, font=font(13))
    y = HEAD_H
    for gt, rows, sh in groups:
        dr.line((0, y + 2, W, y + 2), fill=FAINT)
        dr.text((10, y + 6), gt, fill=INK, font=font(15))
        if sh:
            dr.text((LABEL_W + n * TW + 8, y + 6), "  ".join(f"{k} {100 * v:.0f}%" for k, v in sh.items()),
                    fill=INK, font=font(12))
        y += 26
        for label, col, d, key, view, cmp_d in rows:
            dr.text((10, y + TH // 2 - 8), label, fill=col, font=font(14))
            diffs = []
            for i, s in enumerate(shots):
                a = _png(Image, d, key, s, view)
                x = LABEL_W + i * TW
                if a is not None:
                    im.paste(a.resize((TW - 4, TH - 4)), (x + 2, y + 2))
                else:
                    dr.rectangle((x + 2, y + 2, x + TW - 2, y + TH - 2), outline=FAINT)
                if cmp_d:
                    pct = diff_pct(a, _png(Image, cmp_d, key, s, view))
                    diffs.append(pct)
                    if pct is not None:
                        dr.text((x + 6, y + TH + 2), f"{pct:.1f} %", fill=HOT if pct > 0.05 else FAINT,
                                font=font(12))
            if diffs:
                v = [p for p in diffs if p is not None]
                txt = f"max {max(v):.1f} %  mean {sum(v) / len(v):.1f} %" if v else "no before"
                dr.text((LABEL_W + n * TW + 8, y + TH // 2 - 8), txt, fill=INK, font=font(13))
            y += TH + ROW_PAD
        y += 10
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def _load_set(d: Path) -> tuple[dict, dict]:
    p = d / "manifest.json"
    if not p.exists():
        raise PreviewError(f"{d} is not a states set (no manifest.json): render it first.")
    a = d / "analogues.json"
    return (json.loads(p.read_text(encoding="utf-8")),
            json.loads(a.read_text(encoding="utf-8")) if a.exists() else {})


def states_sheet(set_dir: str | Path, *, families: Mapping[str, str], analogues: Callable[[str], Iterable[str]],
                 names: Sequence[str] | None = None, before_dir: str | Path | None = None, by_family: bool = False,
                 out_dir: str | Path | None = None) -> list[Path]:
    """Sheets of a states set: `<out_dir>/<block>.png` per block (default), or `family_<family>.png` per family
    (`by_family`). `families` maps a block name to its family, `analogues(family)` gives the family's vanilla analogue
    names. `out_dir` defaults to `<set_dir>/sheets`."""
    d = Path(set_dir)
    man, ana = _load_set(d)
    bd, bman = (Path(before_dir), _load_set(Path(before_dir))[0]) if before_dir else (None, {})
    names = list(names or sorted(man))
    out = Path(out_dir) if out_dir else d / "sheets"
    tag = d.name
    suffix = f"  (before: {bd.name})" if bd else ""

    def analogue_groups(fam):
        return [(f"vanilla {a}", _rows("V_" + a, d, None, analogue=True), {}) for a in analogues(fam)
                if "V_" + a in ana]

    def block_group(n):
        return (n, _rows(n, d, bd if bd and n in bman else None), _shares(d, n))

    written = []
    if by_family:
        fams: dict[str, list[str]] = {}
        for n in names:
            fams.setdefault(families[n], []).append(n)
        for fam, ns in fams.items():
            written.append(_draw(f"{tag}: {fam}{suffix}", analogue_groups(fam) + [block_group(n) for n in ns],
                                 out / f"family_{fam}.png"))
    else:
        for n in names:
            written.append(_draw(f"{tag}: {n}{suffix}", analogue_groups(families[n]) + [block_group(n)],
                                 out / f"{n}.png"))
    return written


# ---------------------------------------------------------------------------------------------------- icons
def icon_sheet(ours: Sequence[tuple[str | Path, str]], vanilla: Sequence[tuple[str | Path, str]], out: str | Path, *,
               title: str = "icons vs vanilla", menu: Sequence[tuple[str, str | Path, Sequence[str]]] = (),
               menu_title: str = "") -> Path:
    """`ours` / `vanilla`: [(icon PNG, caption)]. `menu`: G-screen tiles [(kind name, icon PNG, size lines)]."""
    Image, _, ImageDraw, ImageFont = _import_pillow()

    def font(size):
        return _font(ImageFont, size)

    def load(p, size):
        try:
            with Image.open(p) as im:
                return im.convert("RGBA").resize((size, size), Image.LANCZOS)
        except OSError:
            raise PreviewError(f"unreadable icon {p}.") from None

    cols = max(len(ours), len(vanilla), 1)
    W = max(20 + cols * 214, 900)
    H = 40 + 4 * 250 + 3 * 180 + 2 * 120 + (250 if menu else 0)
    sheet = Image.new("RGBA", (W, H), (20, 22, 26, 255))
    d = ImageDraw.Draw(sheet)
    d.text((10, 8), title, font=font(16), fill=(240, 240, 240))

    def row(items, y, bg, label, size, fg, cell=None):
        cell = cell or size + 14
        h = size + 44
        d.rectangle((0, y, sheet.width, y + h), fill=bg)
        d.text((10, y + 4), label, font=font(15), fill=fg)
        for i, (path, cap) in enumerate(items):
            x = 10 + i * cell
            sheet.alpha_composite(load(path, size), (x, y + 24))
            if size >= 128:
                d.text((x, y + 24 + size), cap[:26], font=font(11), fill=fg)
        return y + h + 6

    y = 40
    for bg, fg, tone in ((DARK, (225, 228, 232), "dark"), (LIGHT, (25, 28, 32), "light")):
        y = row(ours, y, bg, f"OURS ({tone}, 200 px)", 200, fg)
        if vanilla:
            y = row(vanilla, y, bg, f"VANILLA ({tone}, 200 px)", 200, fg)
    y = row(ours, y, DARK, "OURS at 128 px", 128, (225, 228, 232))
    if vanilla:
        y = row(vanilla, y, DARK, "VANILLA at 128 px", 128, (225, 228, 232))
        mixed = [x for pair in zip(ours, vanilla) for x in pair]
        y = row(mixed, y, DARK, "OURS at 64 px (alternating with vanilla)", 64, (225, 228, 232), cell=82)
    y = row(ours, y, LIGHT, "OURS at 64 px (light)", 64, (25, 28, 32), cell=82)
    if menu:
        d.rectangle((0, y, sheet.width, y + 230), fill=(20, 22, 26, 255))
        d.text((10, y + 6), menu_title or "G-screen tiles (kinds in OrderingPriority, sizes in DisplaySize order)",
               font=font(15), fill=(240, 240, 240))
        x = 20
        for kind, path, lines in menu:
            d.rectangle((x - 6, y + 34, x + 180, y + 222), fill=(48, 54, 64, 255))
            sheet.alpha_composite(load(path, 96), (x + 40, y + 40))
            d.text((x, y + 140), kind, font=font(14), fill=(250, 200, 90))
            for j, line in enumerate(lines):
                d.text((x, y + 162 + 16 * j), line, font=font(11), fill=(160, 200, 240))
            x += 200
        y += 236
    sheet = sheet.crop((0, 0, W, y + 4))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(out)
    return out
