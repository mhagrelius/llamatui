# llamatui/rasterizer.py
from __future__ import annotations

import io

import pypdfium2 as pdfium


class PdfRasterizer:
    def __init__(self, dpi: int = 200) -> None:
        self._scale = dpi / 72.0

    def page_count(self, pdf_bytes: bytes) -> int:
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            return len(pdf)
        finally:
            pdf.close()

    def rasterize(self, pdf_bytes: bytes, max_pages: int) -> list[bytes]:
        pdf = pdfium.PdfDocument(pdf_bytes)
        out: list[bytes] = []
        try:
            pdf.init_forms()  # render filled AcroForm fields too
        except Exception:
            pass
        try:
            for i in range(min(len(pdf), max_pages)):
                bitmap = pdf[i].render(scale=self._scale, grayscale=True)
                pil = bitmap.to_pil()
                buf = io.BytesIO()
                pil.save(buf, format="PNG")
                out.append(buf.getvalue())
            return out
        finally:
            pdf.close()
