"""Where llamatui keeps per-user data on disk.

One place so the conversations DB and the whisper assets share a single root, independent of
the current working directory.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs


def user_data_dir() -> Path:
    """The per-user data root, e.g. ``%LOCALAPPDATA%\\llamatui`` on Windows."""
    return Path(platformdirs.user_data_dir("llamatui", appauthor=False))


def default_whisper_dir() -> Path:
    """Where ``llamatui --setup-voice`` installs whisper-server + the model."""
    return user_data_dir() / "whisper"


def settings_path() -> Path:
    """Where the persisted Settings file lives (shares the per-user data root)."""
    return user_data_dir() / "settings.json"
