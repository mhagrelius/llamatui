from llamatui.ocr import OcrEngine, FakeVisionClient


def test_fake_vision_client_returns_canned_text():
    vc = FakeVisionClient(["hello", "world"])
    assert vc.ocr_page(b"a") == "hello"
    assert vc.ocr_page(b"b") == "world"


class _FakeRasterizer:
    def __init__(self, total): self._total = total
    def page_count(self, b): return self._total
    def rasterize(self, b, max_pages): return [b"png"] * min(self._total, max_pages)


def test_ocr_pdf_stitches_pages_with_markers():
    eng = OcrEngine(_FakeRasterizer(2), FakeVisionClient(["AAA", "BBB"]))
    res = eng.ocr_pdf(b"%PDF", max_pages=20)
    assert "=== Page 1 ===" in res.text and "AAA" in res.text and "BBB" in res.text
    assert res.pages_done == 2 and res.pages_total == 2 and res.truncated is False


def test_ocr_pdf_truncates_at_cap_and_flags_it():
    eng = OcrEngine(_FakeRasterizer(150), FakeVisionClient(lambda b: "x"))
    res = eng.ocr_pdf(b"%PDF", max_pages=20)
    assert res.pages_done == 20 and res.pages_total == 150 and res.truncated is True


def test_ocr_pdf_zero_pages():
    eng = OcrEngine(_FakeRasterizer(0), FakeVisionClient([]))
    res = eng.ocr_pdf(b"%PDF", max_pages=20)
    assert res.pages_done == 0 and res.text.strip() == ""
