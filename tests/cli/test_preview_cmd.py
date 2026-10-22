import pytest

Image = pytest.importorskip("PIL.Image")


def _png(path, size=(20, 10), color=(200, 30, 30)):
    Image.new("RGB", size, color).save(path)
    return path


def _size(path):
    with Image.open(path) as im:
        return im.size


def test_sheet_dir_grid_and_stdout(run, tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    for n in ("b", "a", "c"):
        _png(d / f"{n}.png")
    out = tmp_path / "s.png"
    code, so, se = run("sheet", str(d), "--out", str(out), "--cols", "2", "--thumb", "40")
    assert code == 0 and se == ""
    w, h = _size(out)
    assert so.strip() == f"{out} {w}x{h}"
    assert w > 2 * 40 and h > 2 * 20  # 2 columns, 2 rows of 3 images


def test_sheet_labels_and_title_add_height(run, tmp_path):
    _png(tmp_path / "a.png")
    plain, labeled = tmp_path / "p.png", tmp_path / "l.png"
    run("sheet", str(tmp_path / "a.png"), "--out", str(plain), "--thumb", "40")
    code, _, _ = run("sheet", str(tmp_path / "a.png"), "--out", str(labeled), "--thumb", "40", "--labels", "--title", "T")
    assert code == 0 and _size(labeled)[1] > _size(plain)[1]


def test_sheet_glob_sorted_jpg_and_skips_own_output(run, tmp_path):
    for n in ("a", "b"):
        _png(tmp_path / f"{n}.png")
    out = tmp_path / "sheet.jpg"
    assert run("sheet", str(tmp_path), "--out", str(out))[0] == 0
    first = _size(out)
    assert run("sheet", str(tmp_path), "--out", str(out))[0] == 0  # rerun must not ingest its own output
    assert _size(out) == first
    assert Image.open(out).format == "JPEG"
    code, so, _ = run("sheet", str(tmp_path / "*.png"), "--out", str(tmp_path / "g.png"))
    assert code == 0


def test_sheet_errors(run, tmp_path):
    code, so, se = run("sheet", str(tmp_path / "nothing*.png"), "--out", str(tmp_path / "o.png"))
    assert code == 1 and so == "" and "no images matched" in se and se.count("\n") == 1
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    code, so, se = run("sheet", str(bad), "--out", str(tmp_path / "o.png"))
    assert code == 1 and "unreadable image" in se and "bad.png" in se
    _png(tmp_path / "a.png")
    code, _, se = run("sheet", str(tmp_path / "a.png"), "--out", str(tmp_path / "o.txt"))
    assert code == 1 and ".png" in se


def test_pair_same_height_and_diff(run, tmp_path):
    _png(tmp_path / "b.png", (20, 10))
    _png(tmp_path / "a.png", (40, 20), (30, 30, 200))
    out = tmp_path / "p.png"
    code, so, _ = run("pair", str(tmp_path / "b.png"), str(tmp_path / "a.png"), "--out", str(out))
    assert code == 0
    w2, h2 = _size(out)
    assert so.strip() == f"{out} {w2}x{h2}"
    out3 = tmp_path / "p3.png"
    run("pair", str(tmp_path / "b.png"), str(tmp_path / "a.png"), "--out", str(out3), "--diff", "--labels", "x,y")
    w3, h3 = _size(out3)
    assert w3 > w2 and h3 > h2  # third panel and a label strip


def test_pair_diff_marks_changed_pixels(run, tmp_path):
    base = Image.new("RGB", (16, 16), (50, 50, 50))
    other = base.copy()
    other.putpixel((8, 8), (255, 255, 255))
    base.save(tmp_path / "b.png")
    other.save(tmp_path / "a.png")
    out = tmp_path / "d.png"
    run("pair", str(tmp_path / "b.png"), str(tmp_path / "a.png"), "--out", str(out), "--diff")
    im = Image.open(out).convert("RGB")
    third = im.crop((im.width - 16, im.height - 16, im.width, im.height))
    px = third.load()
    reds = [px[x, y] for x in range(16) for y in range(16) if px[x, y][0] > 200 and px[x, y][1] < 80]
    assert len(reds) >= 1


def test_pair_errors(run, tmp_path):
    _png(tmp_path / "a.png")
    code, _, se = run("pair", str(tmp_path / "missing.png"), str(tmp_path / "a.png"), "--out", str(tmp_path / "o.png"))
    assert code == 1 and "unreadable image" in se


def test_out_is_directory_and_unwritable(run, tmp_path):
    _png(tmp_path / "a.png")
    d = tmp_path / "dir.png"
    d.mkdir()
    code, so, se = run("sheet", str(tmp_path / "a.png"), "--out", str(d))
    assert code == 1 and so == "" and "cannot write" in se and se.count("\n") == 1
    code, _, se = run("pair", str(tmp_path / "a.png"), str(tmp_path / "a.png"), "--out", str(d))
    assert code == 1 and "cannot write" in se
    blocker = tmp_path / "file"
    blocker.write_text("x")
    code, _, se = run("sheet", str(tmp_path / "a.png"), "--out", str(blocker / "sub" / "o.png"))
    assert code == 1 and "cannot write" in se


def test_jpeg_and_canvas_size_checks():
    import se2modkit.preview.compose as pv
    with pytest.raises(pv.PreviewError, match=r"too large for JPEG \(70000x10\); use \.png"):
        pv.check_output_size("JPEG", 70000, 10)
    pv.check_output_size("PNG", 70000, 10)
    with pytest.raises(pv.PreviewError, match="--thumb"):
        pv.check_output_size("PNG", 20000, 20000)
    w, h = pv.sheet_size(10, 4, 100, True, True)
    assert (w, h) == (6 + 4 * 106, 30 + 6 + 3 * (100 + pv.STRIP + 6))


def test_huge_sheet_refused_before_allocating(run, tmp_path, monkeypatch):
    import se2modkit.preview.compose as pv
    _png(tmp_path / "a.png")
    monkeypatch.setattr(pv, "MAX_CANVAS", 100)
    code, so, se = run("sheet", str(tmp_path / "a.png"), "--out", str(tmp_path / "o.png"), "--thumb", "50")
    assert code == 1 and so == "" and "megapixels" in se and not (tmp_path / "o.png").exists()
    code, _, se = run("pair", str(tmp_path / "a.png"), str(tmp_path / "a.png"), "--out", str(tmp_path / "o.png"))
    assert code == 1 and "megapixels" in se


def test_memory_error_is_one_sentence(run, tmp_path, monkeypatch):
    _png(tmp_path / "a.png")
    real = Image.new

    def boom(mode, *a, **k):
        if mode == "RGB":
            raise MemoryError
        return real(mode, *a, **k)
    monkeypatch.setattr(Image, "new", boom)
    code, _, se = run("sheet", str(tmp_path / "a.png"), "--out", str(tmp_path / "o.png"))
    assert code == 1 and "memory" in se and se.count("\n") == 1


def test_alpha_only_diff(run, tmp_path):
    Image.new("RGBA", (16, 16), (100, 100, 100, 0)).save(tmp_path / "b.png")
    Image.new("RGBA", (16, 16), (100, 100, 100, 255)).save(tmp_path / "a.png")
    out = tmp_path / "d.png"
    run("pair", str(tmp_path / "b.png"), str(tmp_path / "a.png"), "--out", str(out), "--diff")
    im = Image.open(out).convert("RGB")
    px = im.load()
    assert px[im.width - 8, im.height - 8][0] > 200  # alpha change alone lights the diff panel


def test_gif_and_tga_matched(run, tmp_path):
    Image.new("RGB", (8, 8), (9, 9, 9)).save(tmp_path / "a.gif")
    Image.new("RGB", (8, 8), (9, 9, 9)).save(tmp_path / "b.tga")
    code, _, _ = run("sheet", str(tmp_path / "*"), "--out", str(tmp_path / "o.png"), "--cols", "2", "--thumb", "8")
    assert code == 0 and _size(tmp_path / "o.png")[0] == 6 + 2 * 14

def test_rgba_composited_not_black_and_diff_counts_alpha(run, tmp_path):
    Image.new("RGBA", (16, 16), (255, 255, 255, 0)).save(tmp_path / "b.png")
    clear = Image.new("RGBA", (16, 16), (255, 255, 255, 0))
    clear.putpixel((10, 10), (255, 255, 255, 255))
    clear.save(tmp_path / "a.png")
    out = tmp_path / "o.png"
    assert run("sheet", str(tmp_path / "b.png"), "--out", str(out), "--thumb", "16")[0] == 0
    px = Image.open(out).convert("RGB").getpixel((6 + 8, 6 + 8))
    assert px != (0, 0, 0) and px[0] < 60  # transparent -> sheet background, not the stored RGB (white)
    out2 = tmp_path / "d.png"
    run("pair", str(tmp_path / "b.png"), str(tmp_path / "a.png"), "--out", str(out2), "--diff")
    im = Image.open(out2).convert("RGB")
    third = im.crop((im.width - 16, im.height - 16, im.width, im.height)).load()
    assert any(third[x, y][0] > 200 and third[x, y][1] < 80 for x in range(16) for y in range(16))


def test_16bit_scaled_not_clipped(run, tmp_path):
    import numpy as np
    arr = np.full((8, 8), 40000, dtype=np.uint16)
    Image.fromarray(arr).save(tmp_path / "w.png")
    assert Image.open(tmp_path / "w.png").mode.startswith("I")
    out = tmp_path / "o.png"
    assert run("sheet", str(tmp_path / "w.png"), "--out", str(out), "--thumb", "8")[0] == 0
    v = Image.open(out).convert("RGB").getpixel((6 + 4, 6 + 4))
    assert 140 < v[0] < 170  # 40000/256 = 156, not clipped to 255


def test_non_images_skipped_and_caps(run, tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    _png(d / "a.png")
    (d / "notes.txt").write_text("hi")
    (d / "x.json").write_text("{}")
    assert run("sheet", str(d), "--out", str(tmp_path / "o.png"))[0] == 0
    assert run("sheet", str(d / "*"), "--out", str(tmp_path / "o2.png"))[0] == 0
    code, _, se = run("sheet", str(d), "--out", str(tmp_path / "o.png"), "--thumb", "5000")
    assert code == 1 and "--thumb" in se
    code, _, se = run("sheet", str(d), "--out", str(tmp_path / "o.png"), "--cols", "65")
    assert code == 1 and "--cols" in se


def test_pixel_cap_errors(run, tmp_path, monkeypatch):
    import se2modkit.preview.compose as pv
    _png(tmp_path / "a.png", (20, 10))
    monkeypatch.setattr(pv, "MAX_PIXELS", 100)
    code, _, se = run("sheet", str(tmp_path / "a.png"), "--out", str(tmp_path / "o.png"))
    assert code == 1 and "megapixels" in se
