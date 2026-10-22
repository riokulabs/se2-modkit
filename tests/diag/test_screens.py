from se2modkit.diag import screens

JPG = b"\xff\xd8\xff\xe0" + b"0" * 10
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 10


def test_label_and_ext():
    assert screens.safe_label(" rack 42U / doors!! ") == "rack-42U-doors"
    assert screens.safe_label("../../etc/passwd") == "etc-passwd"
    assert screens.ext_for(JPG) == ".jpg" and screens.ext_for(PNG) == ".png" and screens.ext_for(b"xx") == ".bin"


def test_save_names_and_collisions(tmp_path):
    a = screens.save(JPG, "kvm open", tmp_path)
    b = screens.save(JPG, "kvm open", tmp_path)
    assert a.parent == tmp_path and a.name.endswith("_kvm-open.jpg") and a != b and b.name.endswith("-2.jpg")
    assert a.read_bytes() == JPG
    c = screens.target_path(PNG, None, tmp_path, now=0)
    assert c.suffix == ".png" and "_" not in c.stem
