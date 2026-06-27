# tests/test_rasterizer.py
from pathlib import Path
from llamatui.rasterizer import PdfRasterizer

FIX = Path(__file__).parent / "fixtures" / "two_page_text.pdf"


def test_page_count():
    assert PdfRasterizer().page_count(FIX.read_bytes()) == 2


def test_rasterize_respects_max_pages_and_returns_pngs():
    pages = PdfRasterizer(dpi=120).rasterize(FIX.read_bytes(), max_pages=1)
    assert len(pages) == 1
    assert pages[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_rasterize_all_pages():
    pages = PdfRasterizer(dpi=120).rasterize(FIX.read_bytes(), max_pages=10)
    assert len(pages) == 2
