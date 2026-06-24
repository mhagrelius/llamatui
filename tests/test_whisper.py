"""WhisperServer's interface is the test surface: discovery, discover-then-spawn,
request shaping, output normalization. No real subprocess, no real network — the HTTP
client and the spawn function are injected fakes (like FakeEmbedder in test_graph.py)."""

import pytest

from llamatui.whisper import WhisperServer, WhisperError, _clean_transcript


# ---- pure output normalization ---------------------------------------------------------
def test_clean_transcript_trims():
    assert _clean_transcript("  hello world  \n") == "hello world"

def test_clean_transcript_drops_non_speech_annotations():
    assert _clean_transcript("[BLANK_AUDIO]") == ""
    assert _clean_transcript("(silence)") == ""
    assert _clean_transcript("[ Pause ]") == ""

def test_clean_transcript_keeps_real_speech_with_punctuation():
    assert _clean_transcript("Ship it. (finally)") == "Ship it. (finally)"


# ---- fakes -----------------------------------------------------------------------------
class FakeResp:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

class FakeHTTP:
    """Records GET/POST calls; serves canned responses."""
    def __init__(self, get_ok=True, post_text="hello"):
        self.get_ok = get_ok
        self.post_text = post_text
        self.posts = []
        self.gets = []
    def get(self, url, timeout=None):
        self.gets.append(url)
        return FakeResp(status=200 if self.get_ok else 503)
    def post(self, url, files=None, data=None, timeout=None):
        self.posts.append({"url": url, "files": files, "data": data})
        return FakeResp(text=self.post_text)

class FakeProc:
    def __init__(self):
        self.terminated = False
    def terminate(self):
        self.terminated = True


# ---- available() -----------------------------------------------------------------------
def test_available_true_when_bin_and_model_present(tmp_path):
    (tmp_path / "whisper-server.exe").write_bytes(b"x")
    (tmp_path / "ggml-small.en.bin").write_bytes(b"x")
    ws = WhisperServer(bin_path=str(tmp_path / "whisper-server.exe"),
                       model_path=str(tmp_path / "ggml-small.en.bin"))
    assert ws.available() is True

def test_available_false_when_model_missing(tmp_path):
    (tmp_path / "whisper-server.exe").write_bytes(b"x")
    ws = WhisperServer(bin_path=str(tmp_path / "whisper-server.exe"),
                       model_path=str(tmp_path / "nope.bin"))
    assert ws.available() is False


# ---- discover-then-spawn ---------------------------------------------------------------
def test_adopts_external_server_without_spawning(tmp_path):
    http = FakeHTTP(get_ok=True)
    spawn_calls = []
    def fake_spawn(*a, **k):
        spawn_calls.append((a, k))
        return FakeProc()
    ws = WhisperServer(url="http://127.0.0.1:9999", _client=http, _spawn=fake_spawn)
    ws.ensure_running()
    assert spawn_calls == []                 # adopted, never spawned
    ws.close()                               # must NOT kill an adopted server (nothing to kill)

def test_spawns_when_no_server_answers(tmp_path):
    # first GET (configured-url probe) fails; after spawn, health GET succeeds
    class FlakyHTTP(FakeHTTP):
        def __init__(self):
            super().__init__()
            self._calls = 0
        def get(self, url, timeout=None):
            self.gets.append(url)
            self._calls += 1
            return FakeResp(status=200 if self._calls > 1 else 503)
    http = FlakyHTTP()
    proc = FakeProc()
    ws = WhisperServer(bin_path="whisper/whisper-server.exe",
                       model_path="whisper/ggml-small.en.bin",
                       _client=http, _spawn=lambda *a, **k: proc, health_timeout=2.0)
    ws.ensure_running()
    ws.close()
    assert proc.terminated is True           # we spawned it, so we kill it

def test_concurrent_ensure_running_spawns_once():
    # warm-at-start + transcribe both call ensure_running concurrently on the first dictation.
    import threading
    http = FakeHTTP(get_ok=True)             # health passes on the first poll after spawn
    spawns = []
    ws = WhisperServer(bin_path="whisper/whisper-server.exe",
                       model_path="whisper/ggml-small.en.bin",
                       _client=http, _spawn=lambda *a, **k: (spawns.append(1), FakeProc())[1],
                       health_timeout=2.0)
    threads = [threading.Thread(target=ws.ensure_running) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(spawns) == 1                   # the lock guarantees exactly one spawn


# ---- transcribe ------------------------------------------------------------------------
def test_transcribe_posts_wav_and_returns_text(tmp_path):
    http = FakeHTTP(get_ok=True, post_text="the quick brown fox")
    ws = WhisperServer(url="http://127.0.0.1:9999", _client=http, _spawn=lambda *a, **k: FakeProc())
    out = ws.transcribe(b"RIFFfake")
    assert out == "the quick brown fox"
    assert http.posts[0]["url"].endswith("/inference")
    assert "file" in http.posts[0]["files"]

def test_transcribe_normalizes_non_speech(tmp_path):
    http = FakeHTTP(get_ok=True, post_text="[BLANK_AUDIO]")
    ws = WhisperServer(url="http://127.0.0.1:9999", _client=http, _spawn=lambda *a, **k: FakeProc())
    assert ws.transcribe(b"RIFFfake") == ""
