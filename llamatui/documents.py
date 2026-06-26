"""documents — bytes → text for PDF/DOCX, behind an explicit DocumentResult.

Pure deep module: no llama-server, no Textual, no filesystem confinement. The
caller (Workspace.read_file) owns the <file_contents> envelope and its
boundary neutralization; this module only decides status + text.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

try:  # feature-detect: never hard-fail at import if a dep is absent
    import docx  # python-docx
except ImportError:  # pragma: no cover - exercised via monkeypatch
    docx = None

try:
    import pypdf
except ImportError:  # pragma: no cover - exercised via monkeypatch
    pypdf = None


@dataclass
class DocumentResult:
    status: str  # "extracted" | "not_a_document" | "needs_ocr" | "failed"
    text: str = ""
    reason: str = ""

    @classmethod
    def extracted(cls, text: str) -> "DocumentResult":
        return cls("extracted", text=text)

    @classmethod
    def not_a_document(cls) -> "DocumentResult":
        return cls("not_a_document")

    @classmethod
    def needs_ocr(cls, reason: str) -> "DocumentResult":
        return cls("needs_ocr", reason=reason)

    @classmethod
    def failed(cls, reason: str) -> "DocumentResult":
        return cls("failed", reason=reason)


def extract_document(data: bytes, filename: str) -> DocumentResult:
    """Recognized document -> extracted/needs_ocr/failed; anything else -> not_a_document.

    Extension *triggers* an attempt; magic bytes only *guard* (a mismatch falls
    back to not_a_document, so a mislabeled file reads as binary like before).
    """
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        if not data.startswith(b"%PDF"):
            return DocumentResult.not_a_document()
        if pypdf is None:
            return DocumentResult.failed("install pypdf to read PDF files")
        return _extract_pdf(data)
    if ext == ".docx":
        if data[:4] != b"PK\x03\x04":  # DOCX is a ZIP container
            return DocumentResult.not_a_document()
        if docx is None:
            return DocumentResult.failed("install python-docx to read DOCX files")
        return _extract_docx(data)
    return DocumentResult.not_a_document()


def _extract_pdf(data: bytes) -> DocumentResult:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Many PDFs are "encrypted" with an empty password and read fine;
            # only treat a real (non-empty) password as a failure.
            # pypdf 6.x: is_encrypted stays True after a successful decrypt,
            # so check the return value instead of re-checking is_encrypted.
            try:
                from pypdf import PasswordType
                if reader.decrypt("") == PasswordType.NOT_DECRYPTED:
                    return DocumentResult.failed("encrypted PDF; cannot read")
            except Exception:
                return DocumentResult.failed("encrypted PDF; cannot read")
        # Pages joined by a blank line; no [page N] markers (clean text).
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:  # never crash the TUI on a bad file
        return DocumentResult.failed(f"could not read PDF: {exc}")
    text = "\n\n".join(p for p in pages if p).strip()
    if not text:
        return DocumentResult.needs_ocr(
            "image-only PDF; no text layer — OCR is a separate capability"
        )
    return DocumentResult.extracted(text)


def _extract_docx(data: bytes) -> DocumentResult:
    try:
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        document = docx.Document(io.BytesIO(data))
        lines: list[str] = []
        # Walk body children in document order: python-docx's .paragraphs
        # silently skips tables, which are often the real content.
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                lines.append(Paragraph(child, document).text)
            elif child.tag == qn("w:tbl"):
                for row in Table(child, document).rows:
                    lines.append(" | ".join(cell.text for cell in row.cells))
    except Exception as exc:  # never crash the TUI on a bad file
        return DocumentResult.failed(f"could not read DOCX: {exc}")
    text = "\n".join(lines).strip()
    if not text:
        return DocumentResult.failed("DOCX contains no extractable text")
    return DocumentResult.extracted(text)
