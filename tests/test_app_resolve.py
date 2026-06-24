"""resolve_whisper_dir: dev fallback to ./whisper when it holds the server binary, else the
user-data dir. Pure (cwd injected), so no App is constructed."""

from llamatui.app import resolve_whisper_dir
from llamatui.paths import default_whisper_dir


def test_prefers_local_dir_when_binary_present(tmp_path):
    (tmp_path / "whisper-server.exe").write_bytes(b"x")
    assert resolve_whisper_dir(tmp_path) == tmp_path


def test_falls_back_to_user_data_dir_when_no_local_binary(tmp_path):
    assert resolve_whisper_dir(tmp_path) == default_whisper_dir()
