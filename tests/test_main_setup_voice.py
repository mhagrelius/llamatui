"""--setup-voice fetches the assets and returns WITHOUT launching the TUI."""

import sys

import llamatui.__main__ as entry
import llamatui.app as appmod
from llamatui import setup_voice


def test_setup_voice_fetches_and_does_not_launch_tui(monkeypatch):
    called = {}

    def fake_fetch(dest, **kw):
        called["dest"] = dest
        return dest / "whisper-server.exe"

    def boom(self):
        raise AssertionError("TUI launched on --setup-voice")

    monkeypatch.setattr(setup_voice, "fetch_whisper", fake_fetch)
    monkeypatch.setattr(appmod.LlamaTUI, "run", boom)
    monkeypatch.setattr(sys, "argv", ["llamatui", "--setup-voice"])

    entry.main()
    assert "dest" in called
