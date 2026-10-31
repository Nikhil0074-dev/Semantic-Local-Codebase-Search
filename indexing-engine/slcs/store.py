"""SQLite persistence: metadata, chunks, FTS5 lexical index and float32 vectors in ONE database file.

Why one file: a file's chunks, FTS rows and vectors are replaced in a single transaction, so metadata, text and vectors
cannot drift apart after a crash (see docs/planning section 27). The VectorBackend interface in vectors.py keeps a
dedicated vector database (e.g. LanceDB) swappable if measured scale ever requires it.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

from . import SCHEMA_VERSION
from .errors import CorruptIndexError, IncompatibleIndexError, ModelMismatchError, RepositoryError
from .models import Chunk
from .security import redact
from .text import tokenize

DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repositories(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'registered', created_at TEXT NOT NULL, last_indexed_at TEXT,
  generation INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  rel_path TEXT NOT NULL, language TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
  content_hash TEXT, status TEXT NOT NULL DEFAULT 'indexed', error TEXT, parser TEXT, warning TEXT, indexed_at TEXT,
  UNIQUE(repo_id, rel_path));
CREATE TABLE IF NOT EXISTS chunks(
  cid INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL UNIQUE,
  repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  rel_path TEXT NOT NULL, language TEXT NOT NULL, symbol_name TEXT, symbol_lname TEXT, symbol_kind TEXT NOT NULL,
  start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, part INTEGER NOT NULL DEFAULT 1,
  content TEXT NOT NULL, content_hash TEXT NOT NULL, embed_hash TEXT NOT NULL, embedding BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repo_id);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_sym ON chunks(symbol_lname);
CREATE INDEX IF NOT EXISTS idx_chunks_embed ON chunks(embed_hash);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(symbol, path, body, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS skipped_files(
  repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE, rel_path TEXT NOT NULL, reason TEXT NOT NULL,
  PRIMARY KEY(repo_id, rel_path));
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  mode TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  files_seen INTEGER DEFAULT 0, files_indexed INTEGER DEFAULT 0, files_unchanged INTEGER DEFAULT 0,
  files_failed INTEGER DEFAULT 0, files_removed INTEGER DEFAULT 0, chunks_written INTEGER DEFAULT 0,
  chunks_reused INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0, error TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo_id_for(root: str) -> str:
    return "repo_" + hashlib.sha1(root.encode("utf-8")).hexdigest()[:10]


def _marks(n: int) -> str:
    return ",".join("?" * n)


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._open_check()

    # ------------------------------------------------------------------ connection handling
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    @contextmanager
    def tx(self):
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def _open_check(self) -> None:
        try:
            c = self.conn()
            ver = c.execute("PRAGMA user_version").fetchone()[0]
            if ver > SCHEMA_VERSION:
                raise IncompatibleIndexError(
                    f"Index schema v{ver} is newer than this application (v{SCHEMA_VERSION}). Update the application "
                    f"or delete the index and re-index.")
            if ver == 0:
                for stmt in filter(None, (s.strip() for s in DDL.split(";\n"))):
                    c.execute(stmt)
                c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self.set_meta("schema_version", str(SCHEMA_VERSION))
            else:
                qc = c.execute("PRAGMA quick_check").fetchone()[0]
                if qc != "ok":
                    raise CorruptIndexError("Index failed an integrity check. Delete it and re-index "
                                            "(source code is never modified).")
            self._recover_interrupted()
        except sqlite3.DatabaseError as e:
            raise CorruptIndexError(f"Index database is unreadable ({e}). Delete it and re-index.") from e

    def _recover_interrupted(self) -> None:
        """Commits are per-file, so an interrupted run leaves a consistent index; just record that it was cut short."""
        c = self.conn()
        c.execute("UPDATE jobs SET status='interrupted', finished_at=? WHERE status='running'", (now_iso(),))
        c.execute("UPDATE repositories SET status='interrupted' WHERE status='indexing'")

    # ------------------------------------------------------------------ meta / model compatibility
    def get_meta(self, key: str) -> Optional[str]:
        r = self.conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn().execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, value))

    def check_model(self, model_id: str, dimension: int, *, adopt: bool) -> None:
        """Vectors from different models must never be mixed. `adopt` records the model for an empty index."""
        cur_id, cur_dim = self.get_meta("embedding_model_id"), self.get_meta("embedding_dimension")
        if cur_id is None:
            if adopt:
                self.set_meta("embedding_model_id", model_id)
                self.set_meta("embedding_dimension", str(dimension))
            return
        if cur_id != model_id or int(cur_dim or 0) != int(dimension):
            raise ModelMismatchError(
                f"The index was built with '{cur_id}' (dim {cur_dim}) but the active model is '{model_id}' "
                f"(dim {dimension}). Re-index all repositories to switch models.")

    def reset_for_new_model(self) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM chunks_fts")
            c.execute("DELETE FROM chunks")
            c.execute("DELETE FROM files")
            c.execute("DELETE FROM meta WHERE key IN ('embedding_model_id','embedding_dimension')")
            c.execute("UPDATE repositories SET status='needs_reindex', generation=generation+1, last_indexed_at=NULL")

    # ------------------------------------------------------------------ repositories
    def add_repository(self, root_path: str, name: str) -> dict:
        rid = repo_id_for(root_path)
        try:
            self.conn().execute("INSERT INTO repositories(id,name,root_path,status,created_at) VALUES(?,?,?,?,?)",
                                (rid, name, root_path, "registered", now_iso()))
        except sqlite3.IntegrityError as e:
            raise RepositoryError("This repository is already registered", code="already_registered") from e
        return self.get_repository(rid)

    def get_repository(self, rid: str) -> dict:
        r = self.conn().execute("SELECT * FROM repositories WHERE id=?", (rid,)).fetchone()
        if not r:
            raise RepositoryError(f"Unknown repository '{rid}'", code="unknown_repository")
        return dict(r)

    def list_repositories(self) -> list[dict]:
        return [dict(r) for r in self.conn().execute("SELECT * FROM repositories ORDER BY name, id")]

    def set_repo_status(self, rid: str, status: str, *, indexed: bool = False) -> None:
        if indexed:
            self.conn().execute("UPDATE repositories SET status=?, last_indexed_at=? WHERE id=?", (status, now_iso(), rid))
        else:
            self.conn().execute("UPDATE repositories SET status=? WHERE id=?", (status, rid))

    def set_repo_root(self, rid: str, new_root: str) -> None:
        """Repository relocated on disk: update the root; relative paths stay valid."""
        self.conn().execute("UPDATE repositories SET root_path=?, status='stale' WHERE id=?", (new_root, rid))

    def remove_repository(self, rid: str) -> None:
        """Deletes the repository's index data only. Source files are never touched."""
        self.get_repository(rid)
        with self.tx() as c:
            c.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT cid FROM chunks WHERE repo_id=?)", (rid,))
            c.execute("DELETE FROM repositories WHERE id=?", (rid,))  # cascades to files/chunks/jobs/skipped

    def generation(self, rid: str) -> int:
        r = self.conn().execute("SELECT generation FROM repositories WHERE id=?", (rid,)).fetchone()
        return int(r[0]) if r else -1

    # ------------------------------------------------------------------ files and chunks
    def get_files(self, rid: str) -> dict[str, dict]:
        return {r["rel_path"]: dict(r) for r in self.conn().execute("SELECT * FROM files WHERE repo_id=?", (rid,))}

    def _delete_chunks(self, c: sqlite3.Connection, file_id: int) -> None:
        c.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT cid FROM chunks WHERE file_id=?)", (file_id,))
        c.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))

    def replace_file(self, rid: str, info: dict, chunks: Sequence[Chunk], vectors: np.ndarray) -> None:
        """Atomically swap ALL data for one file: either the new state is fully visible or the old one remains."""
        if len(chunks) != len(vectors):
            raise ValueError("chunks/vectors length mismatch")
        with self.tx() as c:
            row = c.execute("SELECT id FROM files WHERE repo_id=? AND rel_path=?", (rid, info["rel_path"])).fetchone()
            vals = (info["language"], info["size"], info["mtime_ns"], info["content_hash"], info.get("parser"),
                    info.get("warning"), now_iso())
            if row:
                fid = row["id"]
                self._delete_chunks(c, fid)
                c.execute("UPDATE files SET language=?, size=?, mtime_ns=?, content_hash=?, parser=?, warning=?, "
                          "indexed_at=?, status='indexed', error=NULL WHERE id=?", (*vals, fid))
            else:
                cur = c.execute("INSERT INTO files(repo_id,rel_path,language,size,mtime_ns,content_hash,parser,warning,"
                                "indexed_at,status) VALUES(?,?,?,?,?,?,?,?,?, 'indexed')",
                                (rid, info["rel_path"], *vals))
                fid = cur.lastrowid
            for ch, vec in zip(chunks, vectors):
                cur = c.execute(
                    "INSERT INTO chunks(chunk_id,repo_id,file_id,rel_path,language,symbol_name,symbol_lname,symbol_kind,"
                    "start_line,end_line,part,content,content_hash,embed_hash,embedding) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ch.chunk_id, rid, fid, ch.relative_path, ch.language, ch.symbol_name,
                     (ch.symbol_name or "").lower() or None, ch.symbol_kind, ch.start_line, ch.end_line, ch.part,
                     ch.content, ch.content_hash, ch.embed_hash, np.asarray(vec, dtype=np.float32).tobytes()))
                c.execute("INSERT INTO chunks_fts(rowid,symbol,path,body) VALUES(?,?,?,?)",
                          (cur.lastrowid, " ".join(tokenize(ch.symbol_name or "", drop_stopwords=False)),
                           " ".join(tokenize(ch.relative_path, drop_stopwords=False)), " ".join(tokenize(ch.content))))
            c.execute("UPDATE repositories SET generation=generation+1 WHERE id=?", (rid,))

    def touch_file(self, rid: str, rel_path: str, size: int, mtime_ns: int) -> None:
        """Content hash unchanged but mtime moved: remember the new mtime so the next scan is cheap."""
        self.conn().execute("UPDATE files SET size=?, mtime_ns=?, status='indexed', error=NULL WHERE repo_id=? AND rel_path=?",
                            (size, mtime_ns, rid, rel_path))

    def mark_file_error(self, rid: str, info: dict, error: str) -> None:
        """Record a failed (re-)index. Existing chunks are kept (still valid for the old hash); the file is retried next run."""
        err = redact(error)[:500]
        with self.tx() as c:
            row = c.execute("SELECT id FROM files WHERE repo_id=? AND rel_path=?", (rid, info["rel_path"])).fetchone()
            if row:
                c.execute("UPDATE files SET status='error', error=? WHERE id=?", (err, row["id"]))
            else:
                c.execute("INSERT INTO files(repo_id,rel_path,language,size,mtime_ns,content_hash,status,error) "
                          "VALUES(?,?,?,?,?,NULL,'error',?)",
                          (rid, info["rel_path"], info["language"], info["size"], info["mtime_ns"], err))

    def delete_file(self, rid: str, rel_path: str) -> None:
        with self.tx() as c:
            row = c.execute("SELECT id FROM files WHERE repo_id=? AND rel_path=?", (rid, rel_path)).fetchone()
            if row:
                self._delete_chunks(c, row["id"])
                c.execute("DELETE FROM files WHERE id=?", (row["id"],))
                c.execute("UPDATE repositories SET generation=generation+1 WHERE id=?", (rid,))

    def reusable_embeddings(self, embed_hashes: Iterable[str]) -> dict[str, bytes]:
        """Vectors already computed for identical embed text (same model by construction) - skips re-embedding."""
        hashes = list(set(embed_hashes))
        out: dict[str, bytes] = {}
        for i in range(0, len(hashes), 500):
            part = hashes[i:i + 500]
            for r in self.conn().execute(f"SELECT embed_hash, embedding FROM chunks WHERE embed_hash IN ({_marks(len(part))}) "
                                         f"GROUP BY embed_hash", part):
                out[r[0]] = r[1]
        return out

    def replace_skipped(self, rid: str, items: Iterable[tuple[str, str]]) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM skipped_files WHERE repo_id=?", (rid,))
            c.executemany("INSERT OR REPLACE INTO skipped_files(repo_id,rel_path,reason) VALUES(?,?,?)",
                          [(rid, p, r) for p, r in items])

    # ------------------------------------------------------------------ retrieval primitives
    def fts_search(self, terms: Sequence[str], rids: Sequence[str], languages: Optional[Sequence[str]], limit: int):
        if not terms or not rids:
            return []
        match = " OR ".join(f'"{t}"' for t in dict.fromkeys(terms))
        sql = ("SELECT c.cid, bm25(chunks_fts, 5.0, 2.0, 1.0) AS s FROM chunks_fts JOIN chunks c ON c.cid=chunks_fts.rowid "
               f"WHERE chunks_fts MATCH ? AND c.repo_id IN ({_marks(len(rids))})")
        args: list = [match, *rids]
        if languages:
            sql += f" AND c.language IN ({_marks(len(languages))})"
            args += list(languages)
        sql += " ORDER BY s LIMIT ?"
        args.append(limit)
        return [(r[0], r[1]) for r in self.conn().execute(sql, args)]

    def symbol_search(self, q: str, rids: Sequence[str], languages: Optional[Sequence[str]], limit: int):
        """Returns [(cid, tier)] with tier 0 exact, 1 exact last segment (Class.method), 2 prefix, 3 substring."""
        if not q or not rids:
            return []
        ql = q.lower()
        esc = ql.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql = ("SELECT cid, symbol_lname FROM chunks WHERE part=1 AND symbol_lname IS NOT NULL AND symbol_lname LIKE ? ESCAPE '\\' "
               f"AND repo_id IN ({_marks(len(rids))})")
        args: list = [f"%{esc}%", *rids]
        if languages:
            sql += f" AND language IN ({_marks(len(languages))})"
            args += list(languages)
        sql += " LIMIT 500"
        out = []
        for cid, name in self.conn().execute(sql, args):
            last = name.rsplit(".", 1)[-1]
            tier = 0 if name == ql else 1 if last == ql else 2 if (name.startswith(ql) or last.startswith(ql)) else 3
            out.append((cid, tier))
        out.sort(key=lambda t: (t[1], t[0]))
        return out[:limit]

    def literal_search(self, text: str, rids: Sequence[str], languages: Optional[Sequence[str]], limit: int):
        if not text or not rids:
            return []
        sql = f"SELECT cid FROM chunks WHERE instr(content, ?) > 0 AND repo_id IN ({_marks(len(rids))})"
        args: list = [text, *rids]
        if languages:
            sql += f" AND language IN ({_marks(len(languages))})"
            args += list(languages)
        sql += " ORDER BY (symbol_name IS NULL), cid LIMIT ?"
        args.append(limit)
        return [r[0] for r in self.conn().execute(sql, args)]

    def get_chunks(self, cids: Sequence[int]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        cids = list(cids)
        for i in range(0, len(cids), 500):
            part = cids[i:i + 500]
            q = ("SELECT c.cid, c.chunk_id, c.repo_id, c.rel_path, c.language, c.symbol_name, c.symbol_kind, c.start_line, "
                 "c.end_line, c.part, c.content, c.content_hash, r.name AS repo_name, r.root_path, f.content_hash AS file_hash, "
                 f"f.size AS file_size, f.mtime_ns AS file_mtime FROM chunks c JOIN repositories r ON r.id=c.repo_id "
                 f"JOIN files f ON f.id=c.file_id WHERE c.cid IN ({_marks(len(part))})")
            for r in self.conn().execute(q, part):
                out[r["cid"]] = dict(r)
        return out

    def get_chunk_by_chunk_id(self, chunk_id: str) -> Optional[dict]:
        r = self.conn().execute("SELECT cid FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
        return self.get_chunks([r[0]]).get(r[0]) if r else None

    def iter_vectors(self, rid: str):
        yield from self.conn().execute("SELECT cid, language, embedding FROM chunks WHERE repo_id=?", (rid,))

    # ------------------------------------------------------------------ jobs and stats
    def create_job(self, rid: str, mode: str) -> int:
        cur = self.conn().execute("INSERT INTO jobs(repo_id,mode,status,started_at) VALUES(?,?,?,?)",
                                  (rid, mode, "running", now_iso()))
        return int(cur.lastrowid)

    def update_job(self, job_id: int, **fields) -> None:
        allowed = {"files_seen", "files_indexed", "files_unchanged", "files_failed", "files_removed", "chunks_written",
                   "chunks_reused", "skipped", "status", "error", "finished_at"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if sets:
            self.conn().execute(f"UPDATE jobs SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), job_id))

    def latest_job(self, rid: str) -> Optional[dict]:
        r = self.conn().execute("SELECT * FROM jobs WHERE repo_id=? ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
        return dict(r) if r else None

    def repo_stats(self, rid: str) -> dict:
        c = self.conn()
        files = c.execute("SELECT COUNT(*) FROM files WHERE repo_id=? AND status IN ('indexed','error') AND content_hash IS NOT NULL",
                          (rid,)).fetchone()[0]
        chunks = c.execute("SELECT COUNT(*) FROM chunks WHERE repo_id=?", (rid,)).fetchone()[0]
        errors = c.execute("SELECT COUNT(*) FROM files WHERE repo_id=? AND status='error'", (rid,)).fetchone()[0]
        skipped = c.execute("SELECT COUNT(*) FROM skipped_files WHERE repo_id=?", (rid,)).fetchone()[0]
        langs = {r[0]: r[1] for r in c.execute(
            "SELECT language, COUNT(*) FROM files WHERE repo_id=? AND content_hash IS NOT NULL GROUP BY language ORDER BY 2 DESC", (rid,))}
        return {"files": files, "chunks": chunks, "file_errors": errors, "skipped": skipped, "languages": langs}

    def list_file_errors(self, rid: str) -> list[dict]:
        return [dict(r) for r in self.conn().execute(
            "SELECT rel_path, language, error FROM files WHERE repo_id=? AND status='error' ORDER BY rel_path", (rid,))]

    def list_skipped(self, rid: str) -> list[dict]:
        return [dict(r) for r in self.conn().execute(
            "SELECT rel_path, reason FROM skipped_files WHERE repo_id=? ORDER BY reason, rel_path", (rid,))]

    def list_warnings(self, rid: str) -> list[dict]:
        return [dict(r) for r in self.conn().execute(
            "SELECT rel_path, parser, warning FROM files WHERE repo_id=? AND warning IS NOT NULL ORDER BY rel_path", (rid,))]
