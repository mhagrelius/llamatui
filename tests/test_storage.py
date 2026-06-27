from llamatui.storage import connect, Store


def test_image_roundtrip_and_orphan_sweep(tmp_path):
    from llamatui.storage import Store
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
