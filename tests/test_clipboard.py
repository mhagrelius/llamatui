import io
from PIL import Image
from llamatui.clipboard import grab_from


def _pil(w=50, h=50):
    return Image.new("RGB", (w, h), (9, 9, 9))


def _png_bytes():
    buf = io.BytesIO(); _pil().save(buf, format="PNG"); return buf.getvalue()


def test_grab_bitmap_returns_one_attachment():
    out = grab_from(_pil(2000, 100), max_edge=1568, read_file=lambda p: b"")
    assert len(out.attachments) == 1
    assert out.skipped == []


def test_grab_none_is_empty():
    out = grab_from(None, max_edge=1568, read_file=lambda p: b"")
    assert out.attachments == [] and out.skipped == []


def test_grab_file_list_keeps_images_skips_others():
    files = ["a.png", "notes.pdf", "b.JPG"]
    out = grab_from(files, max_edge=1568, read_file=lambda p: _png_bytes())
    assert len(out.attachments) == 2          # a.png + b.JPG
    assert out.skipped == ["notes.pdf"]
