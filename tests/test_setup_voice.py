"""fetch_whisper lays out whisper-server + model into a dir; the download is an injected
seam so no real network is touched (synthetic zip, like the fakes in test_whisper.py)."""

import io
import zipfile
from pathlib import Path

import pytest

from llamatui.setup_voice import (
    fetch_whisper, WHISPER_RELEASE_URL, MODEL_URL, MODEL_NAME, SERVER_EXE,
)


def _zip_bytes(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, b"x")
    return buf.getvalue()


def _make_download(zip_payload):
    calls = []

    def download(url, dest):
        calls.append(url)
        Path(dest).write_bytes(zip_payload if url == WHISPER_RELEASE_URL else b"MODELDATA")

    download.calls = calls
    return download


def test_fetch_flattens_release_and_downloads_model(tmp_path):
    dl = _make_download(_zip_bytes(["Release/whisper-server.exe", "Release/ggml-cuda.dll"]))
    exe = fetch_whisper(tmp_path, download=dl)
    assert exe == tmp_path / SERVER_EXE
    assert (tmp_path / SERVER_EXE).exists()
    assert (tmp_path / "ggml-cuda.dll").exists()      # flattened out of Release/
    assert not (tmp_path / "Release").exists()
    assert (tmp_path / MODEL_NAME).read_bytes() == b"MODELDATA"
    assert MODEL_URL in dl.calls


def test_zip_without_release_dir_also_works(tmp_path):
    dl = _make_download(_zip_bytes(["whisper-server.exe", "ggml.dll"]))
    exe = fetch_whisper(tmp_path, download=dl)
    assert exe.exists()
    assert (tmp_path / "ggml.dll").exists()


def test_existing_model_is_not_redownloaded(tmp_path):
    (tmp_path / MODEL_NAME).write_bytes(b"ALREADY")
    dl = _make_download(_zip_bytes(["Release/whisper-server.exe"]))
    fetch_whisper(tmp_path, download=dl)
    assert (tmp_path / MODEL_NAME).read_bytes() == b"ALREADY"
    assert MODEL_URL not in dl.calls


def test_missing_server_binary_raises(tmp_path):
    dl = _make_download(_zip_bytes(["Release/not-the-server.exe"]))
    with pytest.raises(RuntimeError, match="whisper-server.exe"):
        fetch_whisper(tmp_path, download=dl)
