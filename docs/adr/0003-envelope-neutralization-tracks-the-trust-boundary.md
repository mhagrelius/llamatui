# Envelope-boundary neutralization tracks the trust boundary, not the tool

**Status:** accepted

Tool output that carries external text into the model is wrapped in a named envelope —
`<fetched_url>` for the web and `<file_contents>` for files, including (new) extracted PDF/DOCX
documents. To stop hostile content from
**forging the closing boundary** and smuggling instructions past it, the wrapper rewrites any
literal envelope token in the body (`</fetched_url>` → `</fetched-url>`, etc.) before
interpolation. The question this ADR settles: *which* envelopes get that neutralization.

The decision is that neutralization tracks the **trust boundary of the content**, not the tool
that produced it:

- **Neutralized** (content is external *and* display-only): `fetch_url` (web) and the
  **document-extraction path** of `read_file` (PDF/DOCX).
- **Left raw** (content is local *and* round-tripped through edits): the plain-text path of
  `read_file` for ordinary workspace files.

So `read_file` is deliberately **asymmetric within itself**: a PDF is neutralized, a `.py` next
to it is not.

## Why not neutralize all of `read_file`

The obvious "just sanitize everything" move makes `read_file` **lossy**, and this repo proves the
collision is real, not hypothetical: the literal envelope tokens appear in our own source and
tests — `llamatui/filesystem.py` (the `<file_contents>` template), `llamatui/webfetch.py`
(`<fetched_url>`), and both their test files. Blanket neutralization would make the model read
`<file-contents` where the file actually says `<file_contents`.

For a harness whose model also **edits** code, a non-faithful read is a correctness risk, not a
cosmetic one: the model can echo the mangled token back through `write_file` and corrupt the
source. Any future file that merely *discusses* these envelopes (e.g. work on llamatui itself)
would hit the same corruption.

## Why neutralize extracted documents but not plain files

The distinguishing property is **whether the text is ever written back**:

- Web pages and extracted PDF/DOCX text are **display-only** — the model reads them, never
  writes them back to a file verbatim. Rewriting a forged boundary token in that text is
  therefore **lossless in practice**.
- Plain workspace files are **round-tripped**: read → reason → `write_file`. Neutralizing them is
  lossy, per the section above.

A PDF/DOCX is also opaque binary the user cannot eyeball and is usually sourced **externally**
(downloaded, emailed) — that is the web threat model, not the "my own source files" model. So
extracted documents belong on the neutralized side even though they arrive through `read_file`.

## Why not an unguessable nonce delimiter instead

A per-read random delimiter would make the boundary **unforgeable** without any mangling,
dissolving the faithful-vs-safe tension for every surface at once. We rejected it for now: it
changes the envelope format that `fetch_url` already established, requires the model to track a
varying delimiter, and is broader than these two "quick win" capabilities warrant. It remains the
clean long-term option if we ever want plain `read_file` neutralized losslessly.

## Consequences

- The neutralization helper (the `<(/?)tag` rewrite already used by `webfetch._envelope`) is
  applied by the document-extraction path, and **not** by the plain-text path of `read_file`.
- `read_file` is intentionally asymmetric: documents neutralize their boundary, ordinary files do
  not. This is by design and is the surprising part a future reader should not "fix" without
  reading this ADR.
- The rule for any **new** ingesting tool: neutralize the envelope if the content is external and
  display-only; leave it raw only if the content is local and gets written back. Stated in
  `CONTEXT.md` alongside the "untrusted data is DATA" invariant.
