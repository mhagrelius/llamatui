from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from PIL import Image

from .images import ImageAttachment, prepare_image

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


@dataclass
class ClipboardGrab:
    attachments: list[ImageAttachment] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def grab_from(raw, *, max_edge: int, read_file: Callable[[str], bytes]) -> ClipboardGrab:
    """Normalize a clipboard payload (PIL image | list-of-paths | None) into a grab."""
    if raw is None:
        return ClipboardGrab()
    if isinstance(raw, Image.Image):
        buf = io.BytesIO(); raw.save(buf, format="PNG")
        return ClipboardGrab([prepare_image(buf.getvalue(), max_edge=max_edge)])
    out = ClipboardGrab()
    for p in raw:
        if Path(p).suffix.lower() in _IMAGE_EXTS:
            out.attachments.append(prepare_image(read_file(p), max_edge=max_edge))
        else:
            out.skipped.append(Path(p).name)
    return out


class Clipboard(Protocol):
    def grab(self, max_edge: int = 1568) -> ClipboardGrab: ...


class PillowClipboard:
    def grab(self, max_edge: int = 1568) -> ClipboardGrab:
        from PIL import ImageGrab
        return grab_from(ImageGrab.grabclipboard(), max_edge=max_edge,
                         read_file=lambda p: Path(p).read_bytes())


class FakeClipboard:
    def __init__(self, raw=None):
        self._raw = raw

    def grab(self, max_edge: int = 1568) -> ClipboardGrab:
        return grab_from(self._raw, max_edge=max_edge, read_file=lambda p: b"")
