from __future__ import annotations

import io

from PIL import Image

from .clipboard import ClipboardGrab
from .images import ImageAttachment


class PasteStaging:
    def __init__(self) -> None:
        self._atts: list[ImageAttachment] = []

    @property
    def pending(self) -> bool:
        return bool(self._atts)

    def add(self, grab: ClipboardGrab) -> str:
        self._atts.extend(grab.attachments)
        notes = []
        if grab.attachments:
            notes.append(f"staged {len(grab.attachments)} image(s)")
        if grab.skipped:
            notes.append("skipped non-image: " + ", ".join(grab.skipped))
        if not grab.attachments and not grab.skipped:
            notes.append("no image on clipboard")
        return "; ".join(notes)

    def chips(self) -> list[str]:
        out = []
        for a in self._atts:
            w, h = Image.open(io.BytesIO(a.data)).size
            out.append(f"📎 image ({w}×{h})")
        return out

    def take(self) -> list[ImageAttachment]:
        atts, self._atts = self._atts, []
        return atts

    def clear(self) -> None:
        self._atts = []
