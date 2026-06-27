from llamatui.images import ImageAttachment, sha256_id


def test_sha256_id_is_stable_hex_of_bytes():
    assert sha256_id(b"abc") == sha256_id(b"abc")
    assert sha256_id(b"abc") != sha256_id(b"abd")
    assert len(sha256_id(b"abc")) == 64


def test_attachment_id_matches_sha256_of_data():
    att = ImageAttachment(data=b"\x89PNG\r\n", media_type="image/png", source="clipboard")
    assert att.id == sha256_id(b"\x89PNG\r\n")
    assert att.trusted is False


import io
from PIL import Image
from llamatui.images import prepare_image, to_content_parts, UNTRUSTED_IMAGE_PREAMBLE


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_prepare_image_downscales_long_edge_to_cap():
    att = prepare_image(_png(4000, 1000), max_edge=1568)
    w, h = Image.open(io.BytesIO(att.data)).size
    assert max(w, h) == 1568
    assert att.media_type == "image/png"
    assert att.source == "clipboard"


def test_prepare_image_never_upscales_small_image():
    att = prepare_image(_png(100, 80), max_edge=1568)
    assert Image.open(io.BytesIO(att.data)).size == (100, 80)


def test_to_content_parts_text_only_has_one_part():
    parts = to_content_parts("hello", [])
    assert len(parts) == 1


def test_to_content_parts_prepends_framing_before_images():
    att = prepare_image(_png(50, 50))
    parts = to_content_parts("look", [att])
    # text + framing + one image == 3 parts
    assert len(parts) == 3
    texts = [getattr(p, "text", "") for p in parts]
    assert any(UNTRUSTED_IMAGE_PREAMBLE in t for t in texts)
