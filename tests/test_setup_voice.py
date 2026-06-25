"""fetch_whisper lays out whisper-server + model into a dir; the download is an injected
seam so no real network is touched (synthetic zip, like the fakes in test_whisper.py)."""

import io
import zipfile
from pathlib import Path

import pytest

from llamatui.setup_voice import (
    fetch_whisper, WHISPER_RELEASE_URL, WHISPER_VERSION, WHISPER_VERSION_MARKER,
    MODEL_URL, MODEL_NAME, SERVER_EXE,
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


def test_rerun_with_matching_version_skips_zip_download(tmp_path):
    dl1 = _make_download(_zip_bytes(["Release/whisper-server.exe", "Release/ggml.dll"]))
    fetch_whisper(tmp_path, download=dl1)                 # first install lays down exe + marker
    dl2 = _make_download(_zip_bytes(["Release/whisper-server.exe"]))
    exe = fetch_whisper(tmp_path, download=dl2)           # re-run to "update" the install
    assert exe.exists()
    assert WHISPER_RELEASE_URL not in dl2.calls           # the ~500 MB zip is NOT re-downloaded


def test_first_fetch_writes_version_marker(tmp_path):
    dl = _make_download(_zip_bytes(["Release/whisper-server.exe"]))
    fetch_whisper(tmp_path, download=dl)
    assert (tmp_path / WHISPER_VERSION_MARKER).read_text().strip() == WHISPER_VERSION


def test_version_bump_refetches_zip(tmp_path):
    dl1 = _make_download(_zip_bytes(["Release/whisper-server.exe"]))
    fetch_whisper(tmp_path, download=dl1)
    (tmp_path / WHISPER_VERSION_MARKER).write_text("v0.0.0-old")   # pinned version moved on
    dl2 = _make_download(_zip_bytes(["Release/whisper-server.exe"]))
    fetch_whisper(tmp_path, download=dl2)
    assert WHISPER_RELEASE_URL in dl2.calls               # stale marker → re-fetch the binary


def test_legacy_binary_without_marker_refetches(tmp_path):
    dl1 = _make_download(_zip_bytes(["Release/whisper-server.exe"]))
    fetch_whisper(tmp_path, download=dl1)
    (tmp_path / WHISPER_VERSION_MARKER).unlink()          # pre-marker install
    dl2 = _make_download(_zip_bytes(["Release/whisper-server.exe"]))
    fetch_whisper(tmp_path, download=dl2)
    assert WHISPER_RELEASE_URL in dl2.calls               # no marker → re-fetch (and stamp it)


def test_skips_binary_but_still_fetches_missing_model(tmp_path):
    dl1 = _make_download(_zip_bytes(["Release/whisper-server.exe"]))
    fetch_whisper(tmp_path, download=dl1)
    (tmp_path / MODEL_NAME).unlink()                      # binary stays, model gone
    dl2 = _make_download(_zip_bytes(["Release/whisper-server.exe"]))
    fetch_whisper(tmp_path, download=dl2)
    assert WHISPER_RELEASE_URL not in dl2.calls           # binary skipped
    assert MODEL_URL in dl2.calls                         # model still fetched
    assert (tmp_path / MODEL_NAME).exists()


def test_missing_server_binary_raises(tmp_path):
    dl = _make_download(_zip_bytes(["Release/not-the-server.exe"]))
    with pytest.raises(RuntimeError, match="whisper-server.exe"):
        fetch_whisper(tmp_path, download=dl)


def test_failed_model_download_leaves_no_partial(tmp_path):
    def download(url, dest):
        from pathlib import Path
        if url == WHISPER_RELEASE_URL:
            Path(dest).write_bytes(_zip_bytes(["Release/whisper-server.exe"]))
        else:
            Path(dest).write_bytes(b"partialdata")   # simulate a partial write...
            raise RuntimeError("network died")        # ...then the download dies

    import pytest
    with pytest.raises(RuntimeError):
        fetch_whisper(tmp_path, download=download)
    assert not (tmp_path / MODEL_NAME).exists()        # no false-positive corrupt model
    assert not (tmp_path / (MODEL_NAME + ".part")).exists()
