from __future__ import annotations

import base64
import json
import urllib.request
from typing import Protocol

OCR_SYSTEM = (
    "You are an OCR engine. Transcribe ALL text in the image verbatim, preserving "
    "reading order. Output only the transcribed text, with no commentary."
)


class VisionClient(Protocol):
    def ocr_page(self, png_bytes: bytes) -> str: ...


class FakeVisionClient:
    def __init__(self, pages):
        self._pages = pages
        self._i = 0

    def ocr_page(self, png_bytes: bytes) -> str:
        if callable(self._pages):
            return self._pages(png_bytes)
        out = self._pages[self._i]
        self._i += 1
        return out


class HttpVisionClient:
    def __init__(self, base_url: str, model: str, *, detail: str = "high", system: str = OCR_SYSTEM):
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._model = model
        self._detail = detail
        self._system = system

    def ocr_page(self, png_bytes: bytes) -> str:
        uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
        body = json.dumps({
            "model": self._model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": self._system},
                {"role": "user", "content": [
                    {"type": "text", "text": "Transcribe this page."},
                    {"type": "image_url", "image_url": {"url": uri, "detail": self._detail}},
                ]},
            ],
        }).encode()
        req = urllib.request.Request(self._url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
