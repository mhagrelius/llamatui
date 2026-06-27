from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def sha256_id(data: bytes) -> str:
    """Stable content hash used to address an image on disk and dedup rows."""
    return hashlib.sha256(data).hexdigest()


@dataclass
class ImageAttachment:
    """An image flowing through the system. Untrusted DATA by default (ADR 0003)."""

    data: bytes
    media_type: str
    source: str  # "clipboard" | "ocr-page" | ...
    trusted: bool = False
    id: str = field(init=False)

    def __post_init__(self) -> None:
        self.id = sha256_id(self.data)
