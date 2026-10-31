"""Plain data containers shared across modules."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class CodeUnit:
    """A symbol-level region of a file. Lines are 1-based and inclusive."""
    name: str
    kind: str                 # function | method | class | interface | struct | enum | trait | impl | table | view | statement ...
    start_line: int
    end_line: int
    parent: Optional[str] = None


@dataclass
class Chunk:
    relative_path: str
    language: str
    symbol_name: Optional[str]
    symbol_kind: str
    start_line: int
    end_line: int
    part: int                 # 1 for the first (or only) piece of a symbol
    parts: int
    content: str
    content_hash: str
    embed_text: str
    embed_hash: str
    chunk_id: str = ""


@dataclass
class SearchResult:
    chunk_id: str
    repository_id: str
    repository_name: str
    relative_path: str
    symbol_name: Optional[str]
    symbol_kind: str
    language: str
    start_line: int
    end_line: int
    snippet: str
    scores: dict[str, Any] = field(default_factory=dict)
    freshness: str = "unknown"     # fresh | stale | missing | unknown
    confidence: str = "low"        # low | medium | high  (agreement heuristic, NOT a probability)
    reasons: list[str] = field(default_factory=list)
    duplicates: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
