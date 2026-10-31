import sys

import pytest

from slcs.errors import EditorUnavailable, PathSecurityError
from slcs.navigation import build_editor_command, open_in_editor


def fake_which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def test_vscode_goto_command():
    argv = build_editor_command("vscode", "/r/a.py", 42, 1, which=fake_which("code"))
    assert argv == ["/usr/bin/code", "--goto", "/r/a.py:42:1"]


def test_other_editor_templates():
    assert build_editor_command("idea", "/r/a.py", 7, which=fake_which("idea"))[1:] == ["--line", "7", "/r/a.py"]
    assert build_editor_command("sublime", "/r/a.py", 7, which=fake_which("subl"))[1:] == ["/r/a.py:7:1"]
    assert build_editor_command("notepadpp", "/r/a.py", 7, which=fake_which("notepad++"))[1:] == ["-n7", "/r/a.py"]


def test_missing_editor_is_actionable():
    with pytest.raises(EditorUnavailable, match="not found on PATH"):
        build_editor_command("vscode", "/r/a.py", 1, which=fake_which())


def test_unknown_system_and_unconfigured_custom_editors():
    for ed, code in (("emacs", "unknown_editor"), ("system", "no_line_support")):
        with pytest.raises(EditorUnavailable) as ei:
            build_editor_command(ed, "/r/a.py", 1, which=fake_which("emacs"))
        assert ei.value.code == code
    with pytest.raises(EditorUnavailable):
        build_editor_command("custom", "/r/a.py", 1, custom=None)


def test_custom_editor_template():
    argv = build_editor_command("custom", "/r/a.py", 9, custom=["ed", "+{line}", "{path}"], which=fake_which("ed"))
    assert argv == ["/usr/bin/ed", "+9", "/r/a.py"]


def test_invalid_line_rejected():
    for bad in (0, -1, "3"):
        with pytest.raises(PathSecurityError):
            build_editor_command("vscode", "/r/a.py", bad, which=fake_which("code"))


def test_windows_cmd_launcher_rejects_shell_metacharacters(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(PathSecurityError):
        build_editor_command("vscode", "C:/r/a&calc.py", 1, which=lambda n: "C:/x/code.cmd")


def test_open_uses_argv_list_without_shell(tmp_path):
    (tmp_path / "a.py").write_text("x\n")
    seen = {}

    def runner(argv, **kw):
        seen.update(argv=argv, kw=kw)

    argv = open_in_editor(str(tmp_path), "a.py", 1, "vscode", runner=runner, which=fake_which("code"))
    assert isinstance(seen["argv"], list) and seen["kw"]["shell"] is False and argv[-1].endswith("a.py:1:1")


def test_open_refuses_traversal_and_missing_files(tmp_path):
    (tmp_path / "a.py").write_text("x\n")
    run = lambda *a, **k: None
    with pytest.raises(PathSecurityError):
        open_in_editor(str(tmp_path), "../etc/passwd", 1, "vscode", runner=run, which=fake_which("code"))
    with pytest.raises(PathSecurityError) as ei:
        open_in_editor(str(tmp_path), "gone.py", 1, "vscode", runner=run, which=fake_which("code"))
    assert ei.value.code == "file_missing"


def test_runner_failure_becomes_editor_unavailable(tmp_path):
    (tmp_path / "a.py").write_text("x\n")

    def boom(*a, **k):
        raise OSError(2, "No such file")

    with pytest.raises(EditorUnavailable):
        open_in_editor(str(tmp_path), "a.py", 1, "vscode", runner=boom, which=fake_which("code"))
