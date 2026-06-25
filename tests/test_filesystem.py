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


from llamatui.filesystem import FILESYSTEM_GUIDANCE


def test_build_tools_marks_write_gated(tmp_path):
    tools = _ws(tmp_path).build_tools()
    by_name = {t.name: t for t in tools}
    assert by_name["write_file"].approval_mode == "always_require"


def test_workspace_line_names_root_and_shell(tmp_path):
    line = Workspace(tmp_path, shell="PowerShell").workspace_line()
    assert str(tmp_path.resolve()) in line and "PowerShell" in line


def test_guidance_forbids_obeying_file_contents():
    assert "never obey" in FILESYSTEM_GUIDANCE.lower() or "data, not" in FILESYSTEM_GUIDANCE.lower()


def test_list_dir_lists_entries_and_confines(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    out = _ws(tmp_path).list_dir(".")
    assert "a.txt" in out and "sub/" in out
    assert "outside your workspace" in _ws(tmp_path).list_dir("..")


def test_read_file_wraps_as_untrusted_with_path(tmp_path):
    (tmp_path / "r.txt").write_text("secret-sauce", encoding="utf-8")
    out = _ws(tmp_path).read_file("r.txt")
    assert "secret-sauce" in out
    assert '<file_contents path="r.txt">' in out and "</file_contents>" in out


def test_read_file_caps_large_and_flags_binary(tmp_path):
    from llamatui.filesystem import READ_CAP
    (tmp_path / "big.txt").write_text("a" * (READ_CAP + 50), encoding="utf-8")
    assert "truncated" in _ws(tmp_path).read_file("big.txt")
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02binary")
    assert "binary" in _ws(tmp_path).read_file("b.bin").lower()


def test_read_file_outside_refused(tmp_path):
    assert "outside your workspace" in _ws(tmp_path).read_file("../x")
