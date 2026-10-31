"""Hybrid retrieval: exact symbol + literal + BM25 lexical + vector similarity, fused with weighted reciprocal rank
fusion (RRF). A high score is a *ranking signal*, never proof of correctness: every result carries evidence signals,
an agreement-based confidence label and a freshness check against the file on disk.
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional, Sequence

from .config import Settings
from .embeddings import Embedder
from .errors import EmbeddingError, ModelMismatchError, PathSecurityError, QueryError, RepositoryError
from .models import SearchResult
from .security import safe_resolve
from .store import Store
from .text import split_words, stem, tokenize, STOPWORDS
from .textio import sha256_bytes, sha256_text, split_lines, decode_bytes
from .vectors import NumpyBruteForce, VectorBackend

RRF_K = 60
MODES = ("auto", "hybrid", "semantic", "lexical", "symbol")
MAX_QUERY_CHARS = 2000

_IDENT = re.compile(r"[A-Za-z_$][\w$]*(?:(?:\.|::|->|#)[A-Za-z_$][\w$]*)*(?:\(\))?")
_PATHLIKE = re.compile(r"[\w./:\\-]+")


def classify_query(q: str) -> str:
    s = q.strip()
    if len(s) > 2 and s[0] == s[-1] and s[0] in "\"'`":
        return "literal"
    if _IDENT.fullmatch(s):
        return "identifier"
    if _PATHLIKE.fullmatch(s) and any(ch in s for ch in "/.-") and " " not in s:
        return "identifier"
    return "natural"


def _strip_ident(q: str) -> str:
    return q.strip().removesuffix("()")


class SearchEngine:
    def __init__(self, store: Store, embedder: Embedder, settings: Settings, backend: Optional[VectorBackend] = None):
        self.store, self.embedder, self.settings = store, embedder, settings
        self._backend = backend

    @property
    def backend(self) -> VectorBackend:
        if self._backend is None:
            self._backend = NumpyBruteForce(self.store, self.embedder.dimension)
        return self._backend

    # ------------------------------------------------------------------ validation
    def _validate(self, query, repository_ids, languages, limit, mode):
        if not isinstance(query, str):
            raise QueryError("Query must be text")
        q = "".join(ch for ch in query if ch == "\n" or ch == "\t" or ord(ch) >= 32).strip()
        if not q:
            raise QueryError("Enter a question or a symbol name to search for", code="empty_query")
        if len(q) > MAX_QUERY_CHARS:
            raise QueryError(f"Query is too long ({len(q)} characters; the limit is {MAX_QUERY_CHARS})", code="query_too_long")
        if mode not in MODES:
            raise QueryError(f"Unknown search mode '{mode}'", code="invalid_mode")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise QueryError("limit must be an integer between 1 and 50", code="invalid_limit")
        if languages is not None and (not isinstance(languages, (list, tuple)) or not all(isinstance(x, str) for x in languages)):
            raise QueryError("languages must be a list of strings", code="invalid_languages")
        repos = {r["id"]: r for r in self.store.list_repositories()}
        if repository_ids:
            for rid in repository_ids:
                if rid not in repos:
                    raise RepositoryError(f"Unknown repository '{rid}'", code="unknown_repository")
            rids = list(repository_ids)
        else:
            rids = list(repos)
        return q, rids, [l.lower() for l in languages] if languages else None, repos

    # ------------------------------------------------------------------ main entry
    def search(self, query: str, repository_ids: Optional[Sequence[str]] = None, languages: Optional[Sequence[str]] = None,
               limit: Optional[int] = None, mode: str = "auto") -> dict:
        t0 = time.perf_counter()
        limit = self.settings.result_limit if limit is None else limit
        q, rids, langs, repos = self._validate(query, repository_ids, languages, limit, mode)
        qclass = classify_query(q)
        pool = max(50, limit * 5)
        warnings: list[str] = []
        lists: dict[str, list] = {}       # retriever -> ranked list of (cid, raw_score)
        semantic_ok = True
        timings: dict[str, float] = {}

        use_symbol = mode in ("auto", "hybrid", "symbol") and qclass in ("identifier", "literal") or mode == "symbol"
        use_lexical = mode in ("auto", "hybrid", "lexical")
        use_semantic = mode in ("auto", "hybrid", "semantic")

        if not rids or not any(self.store.repo_stats(r)["chunks"] for r in rids):
            return self._empty(q, qclass, mode, "No indexed repositories yet. Add a repository and index it first.", t0)

        if use_symbol:
            s0 = time.perf_counter()
            name = _strip_ident(q.strip("\"'`")) if qclass != "natural" else _strip_ident(q)
            lists["symbol"] = [(cid, tier) for cid, tier in self.store.symbol_search(name, rids, langs, pool)]
            lit = self.store.literal_search(q.strip("\"'`") if qclass == "literal" else name, rids, langs, pool)
            lists["literal"] = [(cid, 1) for cid in lit]
            timings["symbol_ms"] = (time.perf_counter() - s0) * 1000

        if use_lexical:
            s0 = time.perf_counter()
            terms = tokenize(q) or tokenize(q, drop_stopwords=False)
            lists["lexical"] = self.store.fts_search(terms, rids, langs, pool)
            timings["lexical_ms"] = (time.perf_counter() - s0) * 1000

        if use_semantic:
            s0 = time.perf_counter()
            try:
                self.store.check_model(self.embedder.model_id, self.embedder.dimension, adopt=False)
                qv = self.embedder.embed([q], "query")[0]
                floor = self.settings.min_semantic_similarity if self.embedder.model_id.startswith("slcs-hash") else -1.0
                lists["semantic"] = [(cid, s) for cid, s in self.backend.query(qv, rids, langs, pool) if s >= floor]
            except (EmbeddingError, ModelMismatchError) as exc:
                if mode == "semantic":
                    raise
                semantic_ok = False
                warnings.append(f"Semantic search is unavailable ({exc}). Showing keyword and symbol results only.")
            timings["semantic_ms"] = (time.perf_counter() - s0) * 1000

        weights = ({"symbol": 3.0, "literal": 2.0, "lexical": 1.0, "semantic": 0.5} if qclass != "natural"
                   else {"symbol": 1.0, "literal": 1.0, "lexical": 1.0, "semantic": 1.0})
        fused: dict[int, float] = {}
        ranks: dict[int, dict[str, int]] = {}
        raw: dict[int, dict[str, float]] = {}
        for name, items in lists.items():
            seen = set()
            r = 0
            for cid, score in items:
                if cid in seen:
                    continue
                seen.add(cid)
                r += 1
                fused[cid] = fused.get(cid, 0.0) + weights[name] / (RRF_K + r)
                ranks.setdefault(cid, {})[name] = r
                raw.setdefault(cid, {})[name] = score

        ordered = sorted(fused, key=lambda c: -fused[c])[: max(limit * 4, 20)]
        rows = self.store.get_chunks(ordered)
        results = self._build(q, ordered, rows, fused, ranks, raw, repos, limit)
        timings["total_ms"] = (time.perf_counter() - t0) * 1000

        message = None
        low = False
        if not results:
            message = "No matching code found. Try different words, a symbol name, or check the language filter."
        elif all(r.confidence == "low" for r in results):
            low = True
            message = "These are weak matches: only one signal supports each result. Verify before relying on them."
        return {"query": q, "query_class": qclass, "mode": mode, "results": [r.to_dict() for r in results],
                "low_confidence": low, "message": message, "warnings": warnings, "semantic_available": semantic_ok and use_semantic,
                "timings_ms": {k: round(v, 2) for k, v in timings.items()}, "model": self.embedder.model_id,
                "highlight": sorted({stem(w) for w in split_words(q) if len(w) >= 3 and w not in STOPWORDS})}

    def _empty(self, q, qclass, mode, message, t0):
        return {"query": q, "query_class": qclass, "mode": mode, "results": [], "low_confidence": False, "message": message,
                "warnings": [], "semantic_available": False, "timings_ms": {"total_ms": round((time.perf_counter() - t0) * 1000, 2)},
                "model": self.embedder.model_id, "highlight": []}

    # ------------------------------------------------------------------ result assembly
    def _build(self, q, ordered, rows, fused, ranks, raw, repos, limit) -> list[SearchResult]:
        out: list[SearchResult] = []
        seen_hash: dict[str, SearchResult] = {}
        seen_symbol: set = set()
        top = fused[ordered[0]] if ordered else 1.0
        qstems = {stem(w) for w in split_words(q) if len(w) >= 2 and w not in STOPWORDS}
        for cid in ordered:
            row = rows.get(cid)
            if not row:
                continue
            if row["content_hash"] in seen_hash:                  # duplicate content in another file/location
                seen_hash[row["content_hash"]].duplicates += 1
                continue
            sym_key = (row["repo_id"], row["rel_path"], row["symbol_name"])
            if row["symbol_name"] and row["symbol_name"] != "<module>" and sym_key in seen_symbol:
                continue                                             # lower-ranked part of a symbol already listed
            seen_symbol.add(sym_key)
            rk, rw = ranks.get(cid, {}), raw.get(cid, {})
            conf = _confidence(rk, rw)
            res = SearchResult(
                chunk_id=row["chunk_id"], repository_id=row["repo_id"], repository_name=row["repo_name"],
                relative_path=row["rel_path"], symbol_name=row["symbol_name"], symbol_kind=row["symbol_kind"],
                language=row["language"], start_line=row["start_line"], end_line=row["end_line"], snippet=row["content"],
                scores={"fused": round(fused[cid], 5), "relative": round(fused[cid] / top, 3),
                        "semantic": round(rw["semantic"], 4) if "semantic" in rw else None,
                        "semantic_rank": rk.get("semantic"), "lexical_rank": rk.get("lexical"),
                        "bm25": round(-rw["lexical"], 3) if "lexical" in rw else None,
                        "symbol_tier": rw.get("symbol") if "symbol" in rk else None, "literal_match": "literal" in rk},
                confidence=conf, reasons=_reasons(row, rk, rw, qstems))
            res.freshness = self._freshness(row)
            if res.freshness != "fresh":
                res.reasons.append({"stale": "File changed since indexing: the shown lines may have moved. Re-index.",
                                    "missing": "File no longer exists at this location. Re-index."}.get(res.freshness, ""))
                res.reasons = [r for r in res.reasons if r]
            seen_hash[row["content_hash"]] = res
            out.append(res)
            if len(out) >= limit:
                break
        return out

    def _freshness(self, row) -> str:
        try:
            path = safe_resolve(row["root_path"], row["rel_path"])
        except PathSecurityError:
            return "missing"
        try:
            st = path.stat()
        except OSError:
            return "missing"
        if st.st_size == row["file_size"] and st.st_mtime_ns == row["file_mtime"]:
            return "fresh"
        try:
            data = path.read_bytes()
        except OSError:
            return "missing"
        if sha256_bytes(data) == row["file_hash"]:
            return "fresh"
        lines = split_lines(decode_bytes(data)[0])
        current = "\n".join(lines[row["start_line"] - 1:row["end_line"]])
        return "fresh" if sha256_text(current) == row["content_hash"] else "stale"


def _confidence(rk: dict, rw: dict) -> str:
    """Agreement heuristic: several independent signals, or an exact symbol hit, => higher confidence. Not a probability."""
    tier = rw.get("symbol") if "symbol" in rk else None
    if tier is not None and tier <= 1:
        return "high"
    strong = [n for n, r in rk.items() if r <= 10]
    if len(strong) >= 2:
        return "high" if len(strong) >= 3 or min(rk.values()) <= 3 else "medium"
    if strong and min(rk.values()) <= 3:
        return "medium"
    return "low"


def _reasons(row, rk, rw, qstems) -> list[str]:
    reasons: list[str] = []
    if "symbol" in rk:
        tier = rw["symbol"]
        label = {0: "exactly matches", 1: "exactly matches the member name of", 2: "starts with the query in", 3: "contains the query in"}[tier]
        reasons.append(f"Query {label} symbol '{row['symbol_name']}'.")
    if rk.get("literal") and "symbol" not in rk:
        reasons.append("The exact text appears in this code.")
    if "lexical" in rk:
        body = set(tokenize(row["content"] + " " + (row["symbol_name"] or "") + " " + row["rel_path"]))
        hit = sorted(qstems & body)
        if hit:
            reasons.append(f"Keyword match on: {', '.join(hit[:6])} (keyword rank #{rk['lexical']}).")
    if "semantic" in rk:
        reasons.append(f"Semantic similarity {rw['semantic']:.2f} (semantic rank #{rk['semantic']}).")
    if not reasons:
        reasons.append("Matched by the ranking pipeline.")
    return reasons
