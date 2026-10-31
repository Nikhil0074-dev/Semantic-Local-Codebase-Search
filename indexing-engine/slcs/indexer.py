"""Initial and incremental indexing with per-file atomic commits.

Failure semantics (all tested):
* embedding service down / bad response -> that file keeps its previous index data and is marked 'error' (retried next run)
* process killed mid-run               -> already-committed files stay valid; the job is marked 'interrupted' on next open
* embedding model changed              -> ModelMismatchError until the user chooses a full re-index
* repository folder missing            -> RepositoryUnavailable; existing index is preserved
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, asdict
from typing import Callable, Optional

import numpy as np

from .chunker import ChunkerConfig, chunk_file
from .config import Settings
from .embeddings import Embedder
from .errors import EmbeddingError, JobCancelled, ModelMismatchError, RepositoryUnavailable
from .parsers import parse_units
from .scanner import ScannedFile, check_content_policy, scan_repository
from .security import redact
from .store import Store, now_iso
from .textio import decode_bytes, sha256_bytes, split_lines

log = logging.getLogger("slcs.indexer")


@dataclass
class JobSummary:
    job_id: int = 0
    status: str = "running"
    files_seen: int = 0
    files_indexed: int = 0
    files_unchanged: int = 0
    files_failed: int = 0
    files_removed: int = 0
    chunks_written: int = 0
    chunks_reused: int = 0
    skipped: int = 0
    error: Optional[str] = None

    def to_dict(self):
        return asdict(self)


class Indexer:
    def __init__(self, store: Store, embedder: Embedder, settings: Settings):
        self.store, self.embedder, self.settings = store, embedder, settings
        self.cfg = ChunkerConfig(max_lines=settings.max_chunk_lines, overlap=settings.chunk_overlap_lines)

    def index_repository(self, rid: str, mode: str = "incremental", cancel: Optional[threading.Event] = None,
                         progress: Optional[Callable[[JobSummary], None]] = None) -> JobSummary:
        repo = self.store.get_repository(rid)
        root = repo["root_path"]
        if not os.path.isdir(root):
            self.store.set_repo_status(rid, "unavailable")
            raise RepositoryUnavailable(f"Repository folder is missing or inaccessible: {os.path.basename(root)}. "
                                        "If it moved, re-register it at the new location.")
        self.store.check_model(self.embedder.model_id, self.embedder.dimension, adopt=True)
        job = JobSummary(job_id=self.store.create_job(rid, mode))
        self.store.set_repo_status(rid, "indexing")
        try:
            self._run(rid, root, mode, job, cancel, progress)
            job.status = "completed"
            self.store.set_repo_status(rid, "indexed" if job.files_failed == 0 else "indexed_with_errors", indexed=True)
        except JobCancelled:
            job.status = "cancelled"
            self.store.set_repo_status(rid, "cancelled")
        except Exception as exc:
            job.status, job.error = "failed", redact(str(exc))[:500]
            self.store.set_repo_status(rid, "failed")
            log.error("indexing failed: %s", type(exc).__name__)
            self._flush(job)
            raise
        self._flush(job, finished=True)
        return job

    # ------------------------------------------------------------------
    def _flush(self, job: JobSummary, finished: bool = True) -> None:
        self.store.update_job(job.job_id, files_seen=job.files_seen, files_indexed=job.files_indexed,
                              files_unchanged=job.files_unchanged, files_failed=job.files_failed,
                              files_removed=job.files_removed, chunks_written=job.chunks_written,
                              chunks_reused=job.chunks_reused, skipped=job.skipped, status=job.status, error=job.error,
                              finished_at=now_iso() if finished else None)

    def _run(self, rid, root, mode, job: JobSummary, cancel, progress) -> None:
        scan = scan_repository(root, self.settings)
        skipped = [(s.rel_path, s.reason) for s in scan.skipped]
        existing = self.store.get_files(rid)
        scanned = {f.rel_path: f for f in scan.files}
        job.files_seen = len(scanned)

        for rel in [p for p in existing if p not in scanned]:       # deleted, renamed away, or newly excluded
            self.store.delete_file(rid, rel)
            job.files_removed += 1

        for n, f in enumerate(scan.files, start=1):
            if cancel is not None and cancel.is_set():
                raise JobCancelled("Indexing cancelled")
            prev = existing.get(f.rel_path)
            unchanged_meta = (prev and mode != "full" and prev["status"] == "indexed" and prev["mtime_ns"] == f.mtime_ns
                              and prev["size"] == f.size and prev["content_hash"])
            if unchanged_meta:
                job.files_unchanged += 1
            else:
                self._index_file(rid, f, prev, mode, job, skipped)
            if progress and (n % 10 == 0 or n == len(scan.files)):
                self._flush(job, finished=False)
                progress(job)
        job.skipped = len(skipped)
        self.store.replace_skipped(rid, skipped)

    def _index_file(self, rid, f: ScannedFile, prev, mode, job: JobSummary, skipped: list) -> None:
        info = {"rel_path": f.rel_path, "language": f.language, "size": f.size, "mtime_ns": f.mtime_ns}
        try:
            raw = _read_limited(f.abs_path, self.settings.max_file_bytes)
        except OSError as exc:
            self._fail(rid, info, f"unreadable: {exc.strerror or type(exc).__name__}", job)
            return
        reason = check_content_policy(raw)
        if reason:
            skipped.append((f.rel_path, reason))
            if prev:                                    # file became unindexable: drop stale data
                self.store.delete_file(rid, f.rel_path)
                job.files_removed += 1
            return
        file_hash = sha256_bytes(raw)
        info["content_hash"] = file_hash
        if prev and mode != "full" and prev["content_hash"] == file_hash and prev["status"] == "indexed":
            self.store.touch_file(rid, f.rel_path, f.size, f.mtime_ns)
            job.files_unchanged += 1
            return
        text, lossy = decode_bytes(raw)
        parsed = parse_units(text, f.language)
        info["parser"] = parsed.parser
        warnings = [w for w in (parsed.warning, "contains undecodable bytes (replaced)" if lossy else None) if w]
        info["warning"] = "; ".join(warnings) or None
        lines = split_lines(text)
        chunks = chunk_file(f.rel_path, f.language, lines, parsed.units, self.cfg)
        try:
            vectors, reused = self._embed(chunks)
        except EmbeddingError as exc:
            self._fail(rid, info, str(exc), job)
            if exc.code == "model_mismatch":
                raise ModelMismatchError(str(exc)) from exc
            return
        self.store.replace_file(rid, info, chunks, vectors)
        job.files_indexed += 1
        job.chunks_written += len(chunks) - reused
        job.chunks_reused += reused

    def _fail(self, rid, info, message: str, job: JobSummary) -> None:
        self.store.mark_file_error(rid, info, message)
        job.files_failed += 1
        log.warning("file failed (%s): %s", info["rel_path"], redact(message)[:120])

    def _embed(self, chunks) -> tuple[np.ndarray, int]:
        dim = self.embedder.dimension
        if not chunks:
            return np.zeros((0, dim), dtype=np.float32), 0
        cached = self.store.reusable_embeddings(c.embed_hash for c in chunks)
        todo = [i for i, c in enumerate(chunks) if c.embed_hash not in cached]
        out = np.zeros((len(chunks), dim), dtype=np.float32)
        for i, c in enumerate(chunks):
            if c.embed_hash in cached:
                out[i] = np.frombuffer(cached[c.embed_hash], dtype=np.float32)
        if todo:
            new = self.embedder.embed([chunks[i].embed_text for i in todo], "document")
            if new.shape != (len(todo), dim):
                raise EmbeddingError(f"Unexpected embedding shape {new.shape}, expected {(len(todo), dim)}",
                                     code="model_mismatch")
            for k, i in enumerate(todo):
                out[i] = new[k]
        return out, len(chunks) - len(todo)


def _read_limited(path: str, limit: int) -> bytes:
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise OSError(0, "file grew beyond the size limit")
    return data
