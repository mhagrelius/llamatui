from pathlib import Path

from llamatui.filesystem import Workspace


def _ws(tmp_path) -> Workspace:
    return Workspace(tmp_path)


def test_confined_accepts_inside_rejects_outside(tmp_path):
    ws = _ws(tmp_path)
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    assert ws._confined("a.txt") == (tmp_path / "a.txt").resolve()
    assert ws._confined("sub/../a.txt") == (tmp_path / "a.txt").resolve()
    assert ws._confined("../escape.txt") is None
    assert ws._confined(str(tmp_path.parent / "escape.txt")) is None


def test_write_file_creates_inside_and_reports_path(tmp_path):
    ws = _ws(tmp_path)
    msg = ws.write_file("notes/todo.md", "buy milk")
    assert (tmp_path / "notes" / "todo.md").read_text(encoding="utf-8") == "buy milk"
    assert "notes/todo.md" in msg.replace("\\", "/")


def test_write_file_outside_refused(tmp_path):
    ws = _ws(tmp_path)
    msg = ws.write_file("../evil.txt", "x")
    assert "outside your workspace" in msg
    assert not (tmp_path.parent / "evil.txt").exists()
