"""WhisperServer — owns a reachable local STT endpoint (whisper.cpp ``whisper-server``).

The whisper *wire-adapter* seam: the one place that knows the server's request shape
(16 kHz mono WAV → ``/inference``) and normalizes its output vocabulary. Discover-then-spawn,
own only what you spawned — see docs/adr/0001-whisper-as-discovered-cuda-service.md.
"""

from __future__ import annotations

import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from shutil import which

import httpx

# whisper emits bracket/paren-wrapped non-speech annotations on silence/noise, e.g.
# "[BLANK_AUDIO]", "(silence)", "[ Pause ]". A transcript that is wholly such a token is empty.
_NON_SPEECH = re.compile(r"^[\[(].*[\])]$", re.DOTALL)


class WhisperError(RuntimeError):
    """Typed failure for spawn/health/transcription problems."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _clean_transcript(raw: str) -> str:
    text = raw.strip()
    if not text or _NON_SPEECH.match(text):
        return ""
    return text


class WhisperServer:
    def __init__(
        self,
        bin_path: str | None = None,
        model_path: str | None = None,
        url: str | None = None,
        whisper_dir: str | Path = "whisper",
        health_timeout: float = 30.0,
        _spawn=subprocess.Popen,
        _client=httpx,
    ) -> None:
        self._dir = Path(whisper_dir)
        self._bin = Path(bin_path) if bin_path else self._discover_bin()
        self._model = Path(model_path) if model_path else (self._dir / "ggml-small.en.bin")
        self._configured_url = url.rstrip("/") if url else None
        self._health_timeout = health_timeout
        self._spawn = _spawn
        self._client = _client
        self._proc = None                 # set ONLY if we spawned the server
        self._endpoint: str | None = None
        self._lock = threading.Lock()     # serializes discover/spawn (warm + transcribe race)

    # ---- discovery -------------------------------------------------------
    def _discover_bin(self) -> Path:
        local = self._dir / "whisper-server.exe"
        if local.exists():
            return local
        found = which("whisper-server")
        return Path(found) if found else local

    def available(self) -> bool:
        """Pure feature-detect: binary AND model both present. No spawn, no stream."""
        return self._bin.exists() and self._model.exists()

    # ---- lifecycle -------------------------------------------------------
    def _healthy(self, base: str) -> bool:
        try:
            r = self._client.get(base + "/", timeout=1.0)
            return r.status_code < 500
        except Exception:
            return False

    def ensure_running(self) -> None:
        """Discover-then-spawn, lazy + idempotent. Adopt an answering server; else spawn one.

        The lock serializes the discover/spawn decision: warm-at-start and the transcribe path
        both call this concurrently on the first dictation, and without it both could spawn.
        """
        if self._endpoint and self._healthy(self._endpoint):
            return
        with self._lock:
            # re-check inside the lock — the other worker may have finished while we waited
            if self._endpoint and self._healthy(self._endpoint):
                return
            if self._configured_url and self._healthy(self._configured_url):
                self._endpoint = self._configured_url      # adopted — leave self._proc None
                return
            port = _free_port()
            base = self._configured_url or f"http://127.0.0.1:{port}"
            try:
                self._proc = self._spawn(
                    [str(self._bin), "--model", str(self._model),
                     "--host", "127.0.0.1", "--port", str(port)],
                    cwd=str(self._bin.parent),
                )
            except Exception as exc:
                raise WhisperError(f"failed to spawn whisper-server: {exc}") from exc
            deadline = time.monotonic() + self._health_timeout
            while time.monotonic() < deadline:
                if self._healthy(base):
                    self._endpoint = base
                    return
                time.sleep(0.2)
            self.close()
            raise WhisperError("whisper-server did not become healthy in time")

    def transcribe(self, wav_bytes: bytes) -> str:
        self.ensure_running()
        assert self._endpoint is not None
        try:
            r = self._client.post(
                self._endpoint + "/inference",
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={"response_format": "text"},
                timeout=120.0,
            )
            r.raise_for_status()
        except Exception as exc:
            raise WhisperError(f"transcription request failed: {exc}") from exc
        return _clean_transcript(r.text)

    def close(self) -> None:
        """Terminate ONLY a subprocess we spawned. Never kill an adopted server."""
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
            self._endpoint = None
