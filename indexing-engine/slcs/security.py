"""Path, URL and log-safety helpers. Everything that touches the filesystem outside the index goes through here."""
from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from .errors import PathSecurityError


def canonical_root(path: str | os.PathLike) -> Path:
    p = Path(path).expanduser()
    try:
        r = p.resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise PathSecurityError(f"Path does not exist or is not accessible: {path}", code="repository_unavailable") from e
    if not r.is_dir():
        raise PathSecurityError("Repository path must be a directory", code="repository_unavailable")
    return r


def refuse_overbroad_root(root: Path) -> None:
    """Indexing '/' or a whole home directory is almost certainly a mistake and would sweep in unrelated data."""
    if root.parent == root:
        raise PathSecurityError("Refusing to index a filesystem root", code="path_too_broad")
    try:
        if root == Path.home().resolve():
            raise PathSecurityError("Refusing to index your entire home directory; pick a project folder",
                                    code="path_too_broad")
    except RuntimeError:
        pass


def safe_resolve(root: str | os.PathLike, relative: str) -> Path:
    """Resolve `relative` under `root`; reject absolute paths, traversal, NULs and symlink escapes."""
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise PathSecurityError("Invalid relative path")
    if os.path.isabs(relative) or re.match(r"^[A-Za-z]:[\\/]", relative) or relative.startswith(("\\\\", "//")):
        raise PathSecurityError("Absolute paths are not accepted")
    parts = re.split(r"[\\/]+", relative)
    if ".." in parts:
        raise PathSecurityError("Path traversal is not allowed")
    root_r = Path(root).resolve()
    target = (root_r / relative).resolve()
    try:
        target.relative_to(root_r)
    except ValueError as e:
        raise PathSecurityError("Resolved path escapes the repository root") from e
    return target


_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token|access[_-]?key)\b(\s*[:=]\s*)(['\"]?)[^\s'\"]{4,}\3"),
     r"\1\2[REDACTED]"),
]


def redact(text: str) -> str:
    for rx, repl in _SECRET_PATTERNS:
        text = rx.sub(repl, text)
    return text


def redact_path(path: str | os.PathLike) -> str:
    """Replace the home directory with ~ so diagnostics do not leak usernames."""
    s = str(path)
    home = str(Path.home())
    return s.replace(home, "~") if home and s.startswith(home) else s


def is_loopback_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def assert_local_url(url: str, allow_remote: bool) -> None:
    if urlparse(url).scheme not in ("http", "https"):
        raise PathSecurityError("Embedding URL must be http(s)", code="invalid_url")
    if not allow_remote and not is_loopback_url(url):
        raise PathSecurityError(
            "Embedding endpoint is not on this machine. Source code would leave the device; "
            "enable 'allow_remote_embedding' only if you accept that.", code="remote_embedding_blocked")
