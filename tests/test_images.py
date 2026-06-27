from llamatui.images import ImageAttachment, sha256_id


def test_sha256_id_is_stable_hex_of_bytes():
    assert sha256_id(b"abc") == sha256_id(b"abc")
    assert sha256_id(b"abc") != sha256_id(b"abd")
    assert len(sha256_id(b"abc")) == 64


def test_attachment_id_matches_sha256_of_data():
    att = ImageAttachment(data=b"\x89PNG\r\n", media_type="image/png", source="clipboard")
    assert att.id == sha256_id(b"\x89PNG\r\n")
    assert att.trusted is False
