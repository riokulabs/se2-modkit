"""Contact sheets and before/after pairs of images (Pillow, imported lazily: `pip install se2-modkit[preview]`)."""

from __future__ import annotations

import glob
import os
from pathlib import Path

__all__ = ["PILLOW_HINT", "PreviewError", "make_pair", "make_sheet"]

PILLOW_HINT = "Pillow is required for sheet/pair: pip install 'se2-modkit[preview]'"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tga", ".tif", ".tiff"}
MAX_CANVAS = 200_000_000  # output pixels (about 600 MB as RGB)
JPEG_MAX_DIM = 65500
MAX_PIXELS = 200_000_000  # per input image
MAX_THUMB = 4096
MAX_COLS = 64
BG = (24, 25, 28)
INK = (232, 232, 232)
STRIP = 18          # label strip height per cell
GAP = 6
DIFF_THRESHOLD = 24  # summed per-channel difference below which a pixel counts as unchanged


class PreviewError(Exception):
    """A one-sentence, user-facing failure."""


def _import_pillow():
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFont
    except ImportError:
        raise PreviewError(PILLOW_HINT) from None
    return Image, ImageChops, ImageDraw, ImageFont


def _font(ImageFont, size: int):
    """`SE2_FONT` (a TTF path) if set and loadable, else Pillow's built-in font."""
    env = os.environ.get("SE2_FONT")
    if env:
        try:
            return ImageFont.truetype(env, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1: fixed-size bitmap font
        return ImageFont.load_default()


def _expand(specs: list[str]) -> list[Path]:
    found: dict[Path, None] = {}
    for spec in specs:
        p = Path(spec)
        if p.is_dir():
            hits = [q for q in p.iterdir() if q.is_file() and q.suffix.lower() in IMAGE_EXTS]
        else:
            hits = [Path(g) for g in glob.glob(spec) if Path(g).is_file() and Path(g).suffix.lower() in IMAGE_EXTS]
        for q in sorted(hits, key=lambda x: (x.name.lower(), str(x))):
            found[q] = None
    return list(found)


def _open(Image, path: Path):
    """(RGB image, alpha mask or None). Transparent pixels are composited onto BG; 16-bit data is scaled to 8-bit."""
    saved = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = None  # we apply our own cap below; restored in the finally
        with Image.open(path) as im:
            if im.width * im.height > MAX_PIXELS:
                raise PreviewError(f"image {path} is larger than {MAX_PIXELS // 1_000_000} megapixels.")
            im.load()
            if im.mode.startswith("I"):  # I, I;16, I;16L, I;16B: scale down instead of clipping
                import numpy as np
                arr = np.asarray(im).astype(np.float64)
                if arr.size and arr.max() > 255:
                    arr = arr / 256.0
                im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L")
            alpha = None
            if im.mode in ("RGBA", "LA", "PA", "La", "RGBa") or "transparency" in im.info:
                rgba = im.convert("RGBA")
                alpha = rgba.getchannel("A")
                flat = Image.new("RGBA", rgba.size, BG + (255,))
                return Image.alpha_composite(flat, rgba).convert("RGB"), alpha
            return im.convert("RGB"), alpha
    except PreviewError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        raise PreviewError(f"unreadable image {path}: {type(e).__name__}.") from None
    except MemoryError:
        raise PreviewError(f"not enough memory to load {path}.") from None
    finally:
        Image.MAX_IMAGE_PIXELS = saved


def _check_out(out: Path) -> str:
    ext = out.suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        raise PreviewError(f"--out {out} must end in .png, .jpg or .jpeg.")
    return "JPEG" if ext in (".jpg", ".jpeg") else "PNG"


def check_output_size(fmt: str, width: int, height: int) -> None:
    """Refuse an output the encoder or memory cannot take, before anything is allocated."""
    if fmt == "JPEG" and (width > JPEG_MAX_DIM or height > JPEG_MAX_DIM):
        raise PreviewError(f"output too large for JPEG ({width}x{height}); use .png.")
    if width * height > MAX_CANVAS:
        raise PreviewError(f"output would be {width}x{height} ({width * height // 1_000_000} megapixels, over "
                           f"{MAX_CANVAS // 1_000_000}); use a smaller --thumb or fewer images per sheet.")


def sheet_size(count: int, cols: int, thumb: int, labels: bool, title: bool) -> tuple[int, int]:
    cols = min(cols, count)
    rows = -(-count // cols)
    return (GAP + cols * (thumb + GAP), (30 if title else 0) + GAP + rows * (thumb + (STRIP if labels else 0) + GAP))


def _save(im, out: Path, fmt: str) -> tuple[int, int]:
    check_output_size(fmt, *im.size)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "JPEG":
            im.save(out, "JPEG", quality=88)
        else:
            im.save(out, "PNG")
    except (OSError, ValueError) as e:
        raise PreviewError(f"cannot write {out}: {type(e).__name__}.") from None
    return im.size


def _make_sheet(specs: list[str], out: Path, *, cols: int = 4, labels: bool = False, title: str | None = None,
               thumb: int = 320) -> tuple[int, int]:
    """Grid of the matched images (sorted by name), `thumb` px per cell side. Returns the output size."""
    Image, _chops, ImageDraw, ImageFont = _import_pillow()
    fmt = _check_out(out)
    out_abs = out.resolve()
    paths = [p for p in _expand(specs) if p.resolve() != out_abs]
    if not paths:
        raise PreviewError("no images matched " + " ".join(specs) + ".")
    if not 1 <= cols <= MAX_COLS:
        raise PreviewError(f"--cols must be between 1 and {MAX_COLS}.")
    if not 1 <= thumb <= MAX_THUMB:
        raise PreviewError(f"--thumb must be between 1 and {MAX_THUMB}.")
    check_output_size(fmt, *sheet_size(len(paths), cols, thumb, labels, bool(title)))
    cols = min(cols, len(paths))
    rows = -(-len(paths) // cols)
    strip = STRIP if labels else 0
    head = 30 if title else 0
    cw, ch = thumb, thumb + strip
    sheet = Image.new("RGB", (GAP + cols * (cw + GAP), head + GAP + rows * (ch + GAP)), BG)
    draw = ImageDraw.Draw(sheet)
    if title:
        draw.text((GAP + 2, 6), title, fill=INK, font=_font(ImageFont, 18))
    small = _font(ImageFont, 12)
    for i, p in enumerate(paths):
        im, _alpha = _open(Image, p)
        im.thumbnail((cw, thumb))
        x, y = GAP + (i % cols) * (cw + GAP), head + GAP + (i // cols) * (ch + GAP)
        sheet.paste(im, (x + (cw - im.width) // 2, y + strip + (thumb - im.height) // 2))
        if labels:
            draw.text((x + 2, y + 2), p.stem, fill=INK, font=small)
    return _save(sheet, out, fmt)


def _make_pair(before: Path, after: Path, out: Path, *, labels: str | None = None, diff: bool = False) -> tuple[int, int]:
    """BEFORE and AFTER side by side at the same height (AFTER scaled to BEFORE's), plus an optional diff panel."""
    Image, ImageChops, ImageDraw, ImageFont = _import_pillow()
    fmt = _check_out(out)
    (a, aa), (b, ba) = _open(Image, before), _open(Image, after)
    strip0 = STRIP if labels else 0
    bw = max(1, round(b.width * a.height / b.height))
    check_output_size(fmt, a.width + bw + (a.width if diff else 0) + GAP * (4 if diff else 3),
                      a.height + strip0 + 2 * GAP)
    if b.height != a.height:
        size = (max(1, round(b.width * a.height / b.height)), a.height)
        b = b.resize(size, Image.LANCZOS)
        ba = ba.resize(size, Image.LANCZOS) if ba else None
    panels = [a, b]
    names = [s.strip() for s in labels.split(",")] if labels else []
    if diff:
        bb = b if b.size == a.size else b.resize(a.size, Image.LANCZOS)
        ba2 = (ba if ba.size == a.size else ba.resize(a.size, Image.LANCZOS)) if ba else None
        delta = ImageChops.difference(a, bb).convert("RGB")
        r, g, bl = delta.split()
        total = ImageChops.add(ImageChops.add(r, g), bl)
        if aa or ba2:  # alpha changes count too
            total = ImageChops.add(total, ImageChops.difference(aa or Image.new("L", a.size, 255),
                                                                ba2 or Image.new("L", a.size, 255)))
        mask = total.point(lambda v: 255 if v >= DIFF_THRESHOLD else 0)
        base = a.convert("L").convert("RGB").point(lambda v: v // 3)
        panels.append(Image.composite(Image.new("RGB", a.size, (255, 40, 40)), base, mask))
        names = (names + ["before", "after"][len(names):])[:2] + ["diff"] if labels else names
    strip = STRIP if labels else 0
    width = sum(p.width for p in panels) + GAP * (len(panels) + 1)
    sheet = Image.new("RGB", (width, a.height + strip + 2 * GAP), BG)
    draw = ImageDraw.Draw(sheet)
    font = _font(ImageFont, 12)
    x = GAP
    for i, p in enumerate(panels):
        sheet.paste(p, (x, GAP + strip))
        if labels and i < len(names):
            draw.text((x + 2, GAP + 2), names[i], fill=INK, font=font)
        x += p.width + GAP
    return _save(sheet, out, fmt)


def _guard(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except MemoryError:
            raise PreviewError("not enough memory for this output; use a smaller --thumb or fewer images.") from None
    wrapper.__doc__ = fn.__doc__
    wrapper.__name__ = fn.__name__.lstrip("_")
    return wrapper


make_sheet = _guard(_make_sheet)
make_pair = _guard(_make_pair)
