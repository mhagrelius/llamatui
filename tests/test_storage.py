from llamatui.storage import connect, Store


def test_image_roundtrip_and_orphan_sweep(tmp_path):
    store = Store(connect(tmp_path / "t.db"))            # use the module's connect()
    conv = store.create_conversation("t", "sys", "m", workspace=None)
    mid = store.add_message(conv, "user", "look")
    store.add_image(mid, 0, "image/png", b"PNGDATA", "clipboard")

    rows = store.get_images(mid)
    assert len(rows) == 1 and rows[0]["media_type"] == "image/png"
    sha = rows[0]["sha256"]
    assert store.image_bytes(sha) == b"PNGDATA"
    assert (tmp_path / "images" / f"{sha}.png").exists()

    store.delete_conversation(conv)
    assert not (tmp_path / "images" / f"{sha}.png").exists()   # swept


def test_orphan_sweep_respects_dedup(tmp_path):
    store = Store(connect(tmp_path / "t.db"))

    conv1 = store.create_conversation("c1", "sys", "m", workspace=None)
    mid1 = store.add_message(conv1, "user", "first")
    store.add_image(mid1, 0, "image/png", b"SHARED", "clipboard")

    conv2 = store.create_conversation("c2", "sys", "m", workspace=None)
    mid2 = store.add_message(conv2, "user", "second")
    store.add_image(mid2, 0, "image/png", b"SHARED", "clipboard")

    sha = store.get_images(mid1)[0]["sha256"]
    img_path = tmp_path / "images" / f"{sha}.png"
    assert img_path.exists()

    # deleting the first conversation must NOT remove the file (conv2 still holds a reference)
    store.delete_conversation(conv1)
    assert img_path.exists(), "shared image was incorrectly swept after first conv deleted"

    # deleting the second conversation must sweep the now-unreferenced file
    store.delete_conversation(conv2)
    assert not img_path.exists(), "shared image was not swept after last reference deleted"
