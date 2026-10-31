"""Application facade used by both the CLI and the local HTTP server. All policy lives here (or below), not in the UI."""
from __future__ import annotations

import logging
import os
import platform
import threading
from pathlib import Path
from typing import Optional

from . import SCHEMA_VERSION, __version__
from .config import API_MUTABLE, Settings, load_settings, save_settings
from .embeddings import Embedder, make_embedder
from .errors import PathSecurityError, RepositoryError, SlcsError
from .indexer import Indexer, JobSummary
from .navigation import open_in_editor
from .search import SearchEngine
from .security import canonical_root, redact_path, refuse_overbroad_root
from .store import Store

log = logging.getLogger("slcs.service")


class AppService:
    def __init__(self, settings: Optional[Settings] = None, embedder: Optional[Embedder] = None):
        self.settings = settings or load_settings()
        logging.basicConfig(level=getattr(logging, self.settings.log_level.upper(), logging.INFO),
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.store = Store(self.settings.db_path)
        self._build(embedder)
        self._threads: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._live: dict[str, JobSummary] = {}
        self._last_error: dict[str, str] = {}
        self._lock = threading.Lock()

    def _build(self, embedder: Optional[Embedder] = None, settings: Optional[Settings] = None) -> None:
        settings = settings or self.settings
        emb = embedder or make_embedder(settings)          # may raise (e.g. blocked remote endpoint) - nothing assigned yet
        self.settings, self.embedder = settings, emb
        self.indexer = Indexer(self.store, self.embedder, self.settings)
        self.search_engine = SearchEngine(self.store, self.embedder, self.settings)

    # ------------------------------------------------------------------ repositories
    def register_repository(self, path: str, name: Optional[str] = None) -> dict:
        root = canonical_root(path)
        refuse_overbroad_root(root)
        nm = (name or root.name or "repository").strip()[:80]
        return self._public_repo(self.store.add_repository(str(root), nm))

    def relocate_repository(self, rid: str, new_path: str) -> dict:
        root = canonical_root(new_path)
        refuse_overbroad_root(root)
        self.store.get_repository(rid)
        self.store.set_repo_root(rid, str(root))
        return self.repository_status(rid)

    def remove_repository(self, rid: str) -> None:
        self.cancel_index(rid)
        self.store.remove_repository(rid)

    def resolve_repository(self, ref: str) -> str:
        repos = self.store.list_repositories()
        for r in repos:
            if ref in (r["id"], r["name"], r["root_path"]):
                return r["id"]
        try:
            p = str(Path(ref).expanduser().resolve())
            for r in repos:
                if r["root_path"] == p:
                    return r["id"]
        except OSError:
            pass
        raise RepositoryError(f"No registered repository matches '{ref}'", code="unknown_repository")

    def _public_repo(self, r: dict) -> dict:
        r = dict(r)
        r["available"] = os.path.isdir(r["root_path"])
        if not r["available"] and r["status"] not in ("indexing",):
            r["status"] = "unavailable"
        return r

    def list_repositories(self) -> list[dict]:
        return [self.repository_status(r["id"], brief=True) for r in self.store.list_repositories()]

    def repository_status(self, rid: str, brief: bool = False) -> dict:
        r = self._public_repo(self.store.get_repository(rid))
        r["stats"] = self.store.repo_stats(rid)
        r["running"] = rid in self._threads and self._threads[rid][0].is_alive()
        job = self._live.get(rid).to_dict() if r["running"] and rid in self._live else self.store.latest_job(rid)
        r["job"] = job
        if rid in self._last_error:
            r["last_error"] = self._last_error[rid]
        if not brief:
            r["skipped"] = self.store.list_skipped(rid)
            r["file_errors"] = self.store.list_file_errors(rid)
            r["warnings"] = self.store.list_warnings(rid)
        return r

    # ------------------------------------------------------------------ indexing
    def start_index(self, rid: str, mode: str = "incremental", background: bool = True) -> Optional[JobSummary]:
        if mode not in ("incremental", "full"):
            raise RepositoryError("mode must be 'incremental' or 'full'", code="invalid_mode")
        self.store.get_repository(rid)
        with self._lock:
            t = self._threads.get(rid)
            if t and t[0].is_alive():
                raise RepositoryError("Indexing is already running for this repository", code="already_running")
            cancel = threading.Event()
            self._last_error.pop(rid, None)

            def work():
                try:
                    self._live[rid] = JobSummary()
                    self.indexer.index_repository(rid, mode, cancel, progress=lambda j: self._live.__setitem__(rid, j))
                except SlcsError as exc:
                    self._last_error[rid] = f"{exc.code}: {exc}"
                except Exception as exc:  # last-resort guard so a worker thread never dies silently
                    log.exception("unexpected indexing failure")
                    self._last_error[rid] = f"internal_error: {type(exc).__name__}"

            th = threading.Thread(target=work, name=f"index-{rid}", daemon=True)
            self._threads[rid] = (th, cancel)
        if background:
            th.start()
            return None
        th.start()
        th.join()
        if rid in self._last_error:
            raise SlcsError(self._last_error[rid])
        return JobSummary(**{k: v for k, v in (self.store.latest_job(rid) or {}).items()
                             if k in JobSummary.__dataclass_fields__})

    def index_blocking(self, rid: str, mode: str = "incremental") -> JobSummary:
        """Synchronous variant that propagates typed errors (used by CLI and tests)."""
        self.store.get_repository(rid)
        return self.indexer.index_repository(rid, mode)

    def cancel_index(self, rid: str) -> bool:
        t = self._threads.get(rid)
        if t and t[0].is_alive():
            t[1].set()
            t[0].join(timeout=30)
            return True
        return False

    def wait(self, rid: str, timeout: float = 120) -> None:
        t = self._threads.get(rid)
        if t:
            t[0].join(timeout)

    # ------------------------------------------------------------------ search / navigation
    def search(self, query, repository_ids=None, languages=None, limit=None, mode="auto") -> dict:
        return self.search_engine.search(query, repository_ids, languages, limit, mode)

    def open_result(self, chunk_id: str, line: Optional[int] = None) -> dict:
        row = self.store.get_chunk_by_chunk_id(chunk_id)
        if not row:
            raise PathSecurityError("Unknown search result. Run the search again.", code="unknown_result")
        target_line = row["start_line"] if line is None else int(line)
        if not row["start_line"] <= target_line <= row["end_line"]:
            raise PathSecurityError("Line is outside the indexed range", code="invalid_line")
        argv = open_in_editor(row["root_path"], row["rel_path"], target_line, self.settings.editor,
                              custom=self.settings.editor_command or None)
        return {"opened": True, "editor": self.settings.editor, "relative_path": row["rel_path"], "line": target_line,
                "command": [os.path.basename(argv[0])] + ["..."]}

    # ------------------------------------------------------------------ model / settings / diagnostics
    def model_status(self) -> dict:
        st = self.embedder.status()
        idx_id, idx_dim = self.store.get_meta("embedding_model_id"), self.store.get_meta("embedding_dimension")
        st["index_model"] = idx_id
        st["index_dimension"] = int(idx_dim) if idx_dim else None
        st["compatible"] = idx_id is None or (idx_id == st.get("model") and (not st.get("dimension") or int(idx_dim or 0) == st["dimension"]))
        return st

    def get_settings(self) -> dict:
        d = self.settings.to_public_dict()
        d["data_dir"] = redact_path(d["data_dir"])
        d.pop("editor_command", None)
        return d

    def update_settings(self, patch: dict) -> dict:
        bad = set(patch) - API_MUTABLE
        if bad:
            raise PathSecurityError(f"Setting(s) cannot be changed through the API: {', '.join(sorted(bad))}", code="setting_not_allowed")
        new = Settings(**{**self.settings.__dict__, **patch})
        new.validate()
        self._build(settings=new)                           # all-or-nothing: only persist settings that actually work
        save_settings(new)
        return self.get_settings()

    def reindex_all_for_current_model(self) -> None:
        """User-confirmed model switch: drops all vectors/chunks (never source files) and marks repos for re-indexing."""
        self.store.reset_for_new_model()

    def diagnostics(self) -> dict:
        repos = self.store.list_repositories()
        return {"version": __version__, "schema_version": SCHEMA_VERSION, "python": platform.python_version(),
                "platform": platform.system(), "data_dir": redact_path(self.settings.data_dir),
                "telemetry": "none", "network_policy": "loopback only unless allow_remote_embedding",
                "model": self.model_status(),
                "repositories": [{"id": r["id"], "status": r["status"], **{k: v for k, v in self.store.repo_stats(r["id"]).items()
                                                                           if k != "languages"}} for r in repos],
                "note": "Diagnostics never include source snippets, queries or absolute project paths."}
