"""Code-aware chunking: one chunk per symbol, module-level 'gap' chunks, and line windows as a documented fallback.

Invariant (tested): chunk.content == "\\n".join(lines[start_line-1:end_line]) for every chunk, so a chunk can always be
re-verified against the file on disk.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .models import Chunk, CodeUnit
from .textio import sha256_text

_ALNUM = re.compile(r"[A-Za-z0-9]")


@dataclass
class ChunkerConfig:
    max_lines: int = 80
    max_chars: int = 6000
    overlap: int = 10
    window: int = 60
    max_embed_chars: int = 3500


def _windows(start: int, end: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """Split [start, end] (1-based inclusive) into overlapping windows of at most `size` lines."""
    if end - start + 1 <= size:
        return [(start, end)]
    out, s = [], start
    step = max(1, size - overlap)
    while s <= end:
        e = min(end, s + size - 1)
        out.append((s, e))
        if e == end:
            break
        s += step
    return out


def _trim(lines: list[str], s: int, e: int) -> tuple[int, int] | None:
    while s <= e and not lines[s - 1].strip():
        s += 1
    while e >= s and not lines[e - 1].strip():
        e -= 1
    return (s, e) if s <= e else None


def _meaningful(lines: list[str], s: int, e: int) -> bool:
    return sum(1 for ln in lines[s - 1:e] if _ALNUM.search(ln)) >= 2


def chunk_file(rel_path: str, language: str, lines: list[str], units: list[CodeUnit],
               cfg: ChunkerConfig | None = None) -> list[Chunk]:
    cfg = cfg or ChunkerConfig()
    n = len(lines)
    regions: list[tuple[int, int, str | None, str]] = []   # start, end, symbol, kind
    covered = [False] * (n + 2)
    for u in sorted(units, key=lambda u: (u.start_line, u.end_line)):
        s, e = max(1, u.start_line), min(n, u.end_line)
        if s > e:
            continue
        regions.append((s, e, u.name, u.kind))
        for i in range(s, e + 1):
            covered[i] = True
    # gaps between symbols (imports, constants, config) become module-level chunks
    i = 1
    while i <= n:
        if covered[i]:
            i += 1
            continue
        j = i
        while j + 1 <= n and not covered[j + 1]:
            j += 1
        t = _trim(lines, i, j)
        if t and _meaningful(lines, *t):
            kind = "module" if units else "text"
            regions.append((t[0], t[1], "<module>" if units else None, kind))
        i = j + 1

    chunks: list[Chunk] = []
    for s, e, sym, kind in sorted(regions):
        size = cfg.window if (sym is None) else cfg.max_lines
        total_chars = sum(len(x) + 1 for x in lines[s - 1:e])
        if total_chars > cfg.max_chars and (e - s + 1) <= size:
            size = max(10, (e - s + 1) // 2 + 1)   # very wide lines: split further
        wins = _windows(s, e, size, cfg.overlap)
        for k, (ws, we) in enumerate(wins, start=1):
            content = "\n".join(lines[ws - 1:we])
            if not content.strip():
                continue
            header = f"{language} {rel_path}\n{kind} {sym or ''}".rstrip()
            embed_text = (header + "\n" + content)[: cfg.max_embed_chars]
            ch = Chunk(
                relative_path=rel_path, language=language, symbol_name=sym, symbol_kind=kind,
                start_line=ws, end_line=we, part=k, parts=len(wins), content=content,
                content_hash=sha256_text(content), embed_text=embed_text, embed_hash=sha256_text(embed_text),
            )
            ch.chunk_id = hashlib.sha1(f"{rel_path}|{ws}|{we}|{ch.content_hash}".encode()).hexdigest()[:20]
            chunks.append(ch)
    return chunks
