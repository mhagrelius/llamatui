"""resolve_whisper_dir: dev fallback to ./whisper when it holds the server binary, else the
user-data dir. Pure (cwd injected), so no App is constructed.

resolve_workspace: precedence chain for the active workspace root. Pure helper (injected
args, no App constructed), so every precedence level is directly exercisable.
"""

from pathlib import Path

from llamatui.app import resolve_whisper_dir, resolve_workspace
from llamatui.paths import default_whisper_dir


def test_prefers_local_dir_when_binary_present(tmp_path):
    (tmp_path / "whisper-server.exe").write_bytes(b"x")
    assert resolve_whisper_dir(tmp_path) == tmp_path


def test_falls_back_to_user_data_dir_when_no_local_binary(tmp_path):
    assert resolve_whisper_dir(tmp_path) == default_whisper_dir()


def test_default_branch_uses_cwd_whisper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "whisper").mkdir()
    (tmp_path / "whisper" / "whisper-server.exe").write_bytes(b"x")
    assert resolve_whisper_dir() == Path("whisper")


# ---------------------------------------------------------------------------
# resolve_workspace precedence tests
# ---------------------------------------------------------------------------

def test_resolve_workspace_conversation_wins_over_all(tmp_path):
    """Per-conversation workspace takes highest precedence."""
    conv = str(tmp_path / "conv_ws")
    settings = str(tmp_path / "settings_ws")
    config = str(tmp_path / "config_ws")
    cwd = str(tmp_path / "cwd")
    assert resolve_workspace(conv, settings, config, cwd) == conv


def test_resolve_workspace_settings_default_wins_over_config_and_cwd(tmp_path):
    """Settings default_workspace beats config.workspace and cwd when no conversation ws."""
    settings = str(tmp_path / "settings_ws")
    config = str(tmp_path / "config_ws")
    cwd = str(tmp_path / "cwd")
    assert resolve_workspace(None, settings, config, cwd) == settings


def test_resolve_workspace_config_wins_over_cwd(tmp_path):
    """config.workspace (from --workspace flag) beats cwd when no conv or settings default."""
    config = str(tmp_path / "config_ws")
    cwd = str(tmp_path / "cwd")
    assert resolve_workspace(None, None, config, cwd) == config


def test_resolve_workspace_falls_back_to_cwd(tmp_path):
    """All higher-precedence sources absent → falls back to the injected cwd string."""
    cwd = str(tmp_path / "cwd")
    assert resolve_workspace(None, None, None, cwd) == cwd


def test_resolve_workspace_empty_string_treated_as_absent(tmp_path):
    """Empty strings are falsy and should not win over a later non-empty value."""
    cwd = str(tmp_path / "cwd")
    assert resolve_workspace("", "", "", cwd) == cwd
