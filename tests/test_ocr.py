from llamatui.ocr import FakeVisionClient


def test_fake_vision_client_returns_canned_text():
    vc = FakeVisionClient(["hello", "world"])
    assert vc.ocr_page(b"a") == "hello"
    assert vc.ocr_page(b"b") == "world"
