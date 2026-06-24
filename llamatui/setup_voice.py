"""Fetch the whisper.cpp runtime (CUDA whisper-server + the small.en model) into a target dir.

Owns the release URLs and the on-disk layout; nothing else knows them. The byte transfer is an
injected seam so tests use a synthetic zip and never hit the network. Values verified live
against the real binary on 2026-06-24 (RTX 5090 / Blackwell; the CUDA 12.4 build PTX-JITs to it).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import httpx

WHISPER_VERSION = "v1.9.1"
WHISPER_ZIP = "whisper-cublas-12.4.0-bin-x64.zip"
WHISPER_RELEASE_URL = (
    f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_VERSION}/{WHISPER_ZIP}"
)
MODEL_NAME = "ggml-small.en.bin"
MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{MODEL_NAME}"
SERVER_EXE = "whisper-server.exe"


def _http_download(url: str, dest: Path) -> None:
    with httpx.stream("GET", url, follow_redirects=True, timeout=None) as r:
        r.raise_for_status()
        with Path(dest).open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


def fetch_whisper(dest: Path, *, download: Callable[[str, Path], None] = _http_download) -> Path:
    """Download + lay out whisper-server and the model into ``dest``. Returns the server exe path.

    The CUDA zips nest everything under ``Release/``; this flattens it so the exe + DLLs sit
    directly in ``dest`` (the default ``--whisper-bin`` location). A present model is not
    re-downloaded. Raises if the server binary is missing after extraction.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / WHISPER_ZIP
        download(WHISPER_RELEASE_URL, zip_path)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dest)

    nested = dest / "Release"
    if nested.is_dir():
        for item in nested.iterdir():
            target = dest / item.name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            shutil.move(str(item), str(dest))
        nested.rmdir()

    exe = dest / SERVER_EXE
    if not exe.exists():
        raise RuntimeError(f"{SERVER_EXE} not found after extracting {WHISPER_ZIP} into {dest}")

    model = dest / MODEL_NAME
    if not model.exists():
        partial = dest / (MODEL_NAME + ".part")
        try:
            download(MODEL_URL, partial)
            os.replace(partial, model)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    return exe
