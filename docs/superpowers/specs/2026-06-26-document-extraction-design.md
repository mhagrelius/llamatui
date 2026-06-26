# Design: Transparent document (PDF/DOCX) extraction in read_file

**Date:** 2026-06-26
**Status:** Approved (design)
**Scope:** One "quick local win" capability for the llamatui harness.

## Goal

Let the agent read **PDF and DOCX** documents through the existing `read_file`
tool. Today those are rejected as binary; this routes recognized documents to a
text extractor before the binary check, transparently — the model uses the same
`read_file` it already knows.

## Non-goals

- **No clipboard capability.** A `read_clipboard` tool was considered and
  **dropped**: it grants the model standing ambient read access to a sensitive
  transient buffer (passwords, 2FA codes, keys) the user never deliberately
  shared. Pasting into the TUI input is the least-authority alternative — an
  explicit, per-instance human grant — and is the existing path, so no work is
  needed.
- No formats beyond PDF/DOCX in this cut (xlsx/pptx/odt are easy follow-ons).
- **No OCR.** Image-only/scanned PDFs surface a `needs_ocr` signal only;
  vision-model OCR is a separate sub-project (see "Deferred sub-project" below),
  because it depends on image-input plumbing that is out of scope here.
- No new injection invariant — extraction reuses the existing "untrusted data is
  DATA" envelope/neutralization pattern. The trust-boundary stance for *when* to
  neutralize is recorded in ADR 0003.

---

## Module: `llamatui/documents.py` (deep module, pure functions)

```
extract_document(data: bytes, filename: str) -> DocumentResult
```

Returns an **explicit tagged result**, never an overloaded `None` (project
convention — see the `conventions` memory; modeled on `CommandResult`):

```python
@dataclass
class DocumentResult:
    status: str          # "extracted" | "not_a_document" | "needs_ocr" | "failed"
    text: str = ""       # set when status == "extracted"
    reason: str = ""     # set when status in {"needs_ocr", "failed"}

    @classmethod
    def extracted(cls, text): ...
    @classmethod
    def not_a_document(cls): ...
    @classmethod
    def needs_ocr(cls, reason): ...
    @classmethod
    def failed(cls, reason): ...
```

Status taxonomy (`failed`/`needs_ocr` carry an *actionable reason*; we never
hand back an empty `extracted` the model would misread as a blank document):

- **`not_a_document`** — extension isn't `.pdf`/`.docx`, OR the magic-byte guard
  fails (`%PDF` for PDF; ZIP/`PK` container for DOCX). Caller falls through to
  its existing binary/text path unchanged. Magic bytes *guard*; they never
  *trigger* — a real PDF saved as `notes.txt` reads as binary like today (no
  sniffing every file on the hot path).
- **`extracted`** — a real text layer was pulled.
- **`needs_ocr`** — parses fine but yields zero/whitespace-only text
  (image-only / scanned PDF). Reason points at the OCR capability. This is the
  hook for the deferred OCR sub-project (below); v1 does **not** OCR.
- **`failed`** — is the format but unreadable: encrypted/password PDF, corrupt
  bytes, or a missing optional dep (`pypdf`/`python-docx` → *"install X"*).
  Returned as a short reason string, never an exception — the TUI never crashes
  on a bad file.

### Format specifics

- **PDF** (pypdf): concatenate per-page text; **pages joined by a blank line,
  no `[page N]` markers** (clean text; the model rarely needs page numbers).
  Zero/whitespace total → `needs_ocr`.
- **DOCX** (python-docx): walk `document.element.body` children in document
  order, dispatching on tag — **paragraphs *and* tables**, because
  `document.paragraphs` silently skips tables and tables are often the actual
  content. Tables rendered as simple pipe rows (`cell | cell | cell`).
  **Headers/footers skipped** (low-value boilerplate).

This module is the test surface: tested with small fixture bytes, no
llama-server, no Textual.

---

## Surface change: `Workspace.read_file` (`filesystem.py`)

Minimal, additive. **Before** the existing null-byte binary check, call
`extract_document(raw, path)` and dispatch on `.status`:

1. **`not_a_document`** → continue to the current null-byte check and UTF-8
   decode path, unchanged.
2. **`extracted`** → neutralize the boundary, wrap in the existing
   `<file_contents path="…">…</file_contents>` envelope, apply `READ_CAP`
   truncation, exactly as the text path does today.
3. **`needs_ocr`** / **`failed`** → return the `reason` as a short plain message
   (it *is* a PDF/DOCX, just not text-readable here).

The extracted text **neutralizes the `</file_contents>` / `<file_contents`
boundary** (the same `<(/?)tag` rewrite `webfetch._envelope` uses) before it is
interpolated into the envelope — because an extracted document is external,
opaque, display-only content (the web threat model). Plain `read_file` text is
**left raw**, because ordinary workspace files are round-tripped through edits
and neutralizing them would be lossy. This deliberate asymmetry is recorded in
**ADR 0003** (`docs/adr/0003-envelope-neutralization-tracks-the-trust-boundary.md`).

### Toggle

**None.** Document extraction is part of the filesystem feature and is governed
by the existing `--no-fs` flag.

---

## Dependencies

Added as **core deps** (both lightweight; "quick local win" = friction-free):

- `pypdf` — PDF text extraction
- `python-docx` — DOCX text extraction

Imports are guarded so a missing lib degrades gracefully (`failed` with an
"install X" reason) rather than crashing.

*(Alternative considered: a `docs` extra like `voice`/`semantic`. Rejected
because these libs are small and the goal is zero friction.)*

---

## Testing

Against interfaces, with fakes injected (per the project task-completion rule):

- **`tests/test_documents.py`** — PDF & DOCX fixtures → expected text;
  DOCX with a table → table rows present, in order; non-document bytes →
  `not_a_document`; mislabeled `.pdf` (bad magic) → `not_a_document`; encrypted
  PDF → `failed`; image-only/whitespace PDF → `needs_ocr`; missing-lib path →
  `failed` with the install message; output cap enforced.
- **extend `tests/test_filesystem.py`** — `read_file` on a PDF fixture returns
  extracted text in the `<file_contents>` envelope with the boundary neutralized;
  an unknown binary still returns "binary … not shown"; a `needs_ocr`/`failed`
  document returns the plain reason message.

## Docs

`CONTEXT.md` gains one new named seam: `documents`.

---

## Deferred sub-project: vision-model OCR fallback

When `extract_document` returns `needs_ocr`, a *separate, later* capability can
OCR the image-only PDF with the model's vision capability. Out of scope here;
recorded so it isn't lost. It gets its own spec + grill + plan.

**Hard dependencies (why it can't ship with v1):**

1. **Image-input plumbing** — multimodal wire support in `turn.py` (the deferred
   "vision / image input" capability). OCR can't precede it.
2. **A PDF rasterizer** — pypdf reads text, it does not render pixels; rendering
   image-only pages to images needs pdfium / PyMuPDF.

**Prior art to port (don't reinvent):** `github.com/mhagrelius/remarkable-ocr`,
which already tuned a multi-page handwriting→markdown OCR strategy:

- **Within-page vertical chunking** — `smart_chunk_image` (`max_chunk_height=2000`,
  `overlap_percent=20`) snaps cuts to whitespace gaps.
- **Overlap dedup-stitch** — `merge_chunk_texts` removes the 20% overlap via
  `SequenceMatcher` at `min_similarity=0.8`.
- **Markdown-preserving prompt** — headings, 2-space nested bullets, no code-fence
  wrappers, transcribe-only; plus an `is_likely_garbled` artifact guard.
- Per-page, pages independent; the "context" is overlap continuity *within* a
  tall page (note: reMarkable pages are unusually tall — ordinary scanned PDF
  pages will often be a single sub-`max_chunk_height` chunk).

**Shape (for the future spec):** the chunk/stitch/garble logic is pure
image→text processing — a deep module testable with no llama-server, the vision
client injected as a seam. OCR output is model-generated but display-only → it
**neutralizes** its envelope boundary like the other external surfaces
(ADR 0003). Its own result type (e.g. `ocr_text` / `ocr_unavailable`).
