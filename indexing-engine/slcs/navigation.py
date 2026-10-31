"""Open a search result in the user's editor, safely.

* The client never supplies a path: it sends a chunk_id; the server resolves the path from the index.
* The target must be inside the registered repository (symlink-safe) and must still exist.
* Editors come from an allowlist of argv templates; nothing is run through a shell.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .errors import EditorUnavailable, PathSecurityError
from .security import safe_resolve

# {path} {line} {col} placeholders. Each entry: (executable names to try, argv template)
EDITOR_TEMPLATES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "vscode": (("code", "code.cmd"), ("--goto", "{path}:{line}:{col}")),
    "cursor": (("cursor", "cursor.cmd"), ("--goto", "{path}:{line}:{col}")),
    "vscodium": (("codium", "codium.cmd"), ("--goto", "{path}:{line}:{col}")),
    "sublime": (("subl", "sublime_text"), ("{path}:{line}:{col}",)),
    "idea": (("idea", "idea64.exe", "idea.sh"), ("--line", "{line}", "{path}")),
    "pycharm": (("pycharm", "pycharm64.exe", "pycharm.sh"), ("--line", "{line}", "{path}")),
    "notepadpp": (("notepad++", "notepad++.exe"), ("-n{line}", "{path}")),
}
_WIN_UNSAFE = set('&|<>^%"\r\n')


def build_editor_command(editor: str, path: str, line: int, col: int = 1, *, custom: Sequence[str] | None = None,
                         which=shutil.which) -> list[str]:
    """Pure function (unit-tested): returns argv. Raises EditorUnavailable if the editor cannot be found."""
    if not isinstance(line, int) or line < 1:
        raise PathSecurityError("Invalid line number")
    if editor == "custom":
        if not custom or "{path}" not in " ".join(custom):
            raise EditorUnavailable("Custom editor command is not configured (edit settings.json: editor_command)")
        exe = which(custom[0])
        if not exe:
            raise EditorUnavailable(f"Editor executable '{custom[0]}' was not found on PATH")
        fmt = {"path": path, "line": str(line), "col": str(col)}
        return [exe] + [a.format(**fmt) for a in custom[1:]]
    if editor == "system":
        raise EditorUnavailable("The system default opener cannot jump to a line", code="no_line_support")
    if editor not in EDITOR_TEMPLATES:
        raise EditorUnavailable(f"Unknown editor '{editor}'", code="unknown_editor")
    names, template = EDITOR_TEMPLATES[editor]
    exe = next((p for n in names if (p := which(n))), None)
    if not exe:
        raise EditorUnavailable(f"'{names[0]}' was not found on PATH. Install its command-line launcher or pick another "
                                f"editor in Settings.")
    if sys.platform.startswith("win") and exe.lower().endswith((".cmd", ".bat")) and any(c in _WIN_UNSAFE for c in path):
        raise PathSecurityError("File path contains characters that are unsafe to pass to a .cmd launcher")
    fmt = {"path": path, "line": str(line), "col": str(col)}
    return [exe] + [a.format(**fmt) for a in template]


def open_in_editor(root: str, rel_path: str, line: int, editor: str, *, custom: Sequence[str] | None = None,
                   runner=subprocess.Popen, which=shutil.which) -> list[str]:
    target: Path = safe_resolve(root, rel_path)
    if not target.is_file():
        raise PathSecurityError("The file no longer exists. Re-index the repository.", code="file_missing")
    argv = build_editor_command(editor, str(target), line, 1, custom=custom, which=which)
    try:
        runner(argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
               close_fds=True)
    except OSError as exc:
        raise EditorUnavailable(f"Could not start the editor: {exc.strerror or exc}") from exc
    return argv
