"""One definition of 'lines' for the whole system, so indexed line numbers match what editors show."""
from __future__ import annotations

import hashlib


def decode_bytes(raw: bytes) -> tuple[str, bool]:
    """Return (text, lossy). UTF-8 (with optional BOM) first; undecodable bytes become U+FFFD."""
    try:
        text = raw.decode("utf-8")
        lossy = False
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        lossy = True
    if text.startswith("\ufeff"):
        text = text[1:]
    return text, lossy


def split_lines(text: str) -> list[str]:
    """Split on LF only and strip trailing CR (CRLF files). A trailing newline does not create a phantom line."""
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()
    return lines


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
