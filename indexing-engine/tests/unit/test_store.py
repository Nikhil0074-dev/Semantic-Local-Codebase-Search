import sqlite3

import numpy as np
import pytest

from slcs import SCHEMA_VERSION
from slcs.errors import CorruptIndexError, IncompatibleIndexError, ModelMismatchError, RepositoryError
from slcs.models import Chunk
from slcs.store import Store


def mk_chunk(path="a.py", sym="alpha", start=1, end=2, content="def alpha():\n  return gamma_value", chunk_id=None):
    return Chunk(path, "python", sym, "function", start, end, 1, 1, content, "h" + content, content, "e" + content,
                 chunk_id or f"{path}:{start}:{end}")


def vec(n=1, dim=4):
    return np.ones((n, dim), dtype=np.float32)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "i.sqlite3")
    s.add_repository("/tmp/r", "r")
    yield s
    s.close()


def rid(store):
    return store.list_repositories()[0]["id"]


def info(path="a.py"):
    return {"rel_path": path, "language": "python", "size": 1, "mtime_ns": 1, "content_hash": "x"}


def test_schema_version_recorded(store):
    assert store.get_meta("schema_version") == str(SCHEMA_VERSION)


def test_newer_schema_is_refused(tmp_path):
    p = tmp_path / "i.sqlite3"
    Store(p).close()
    c = sqlite3.connect(p); c.execute("PRAGMA user_version=99"); c.close()
    with pytest.raises(IncompatibleIndexError):
        Store(p)


def test_corrupt_file_gives_actionable_error(tmp_path):
    p = tmp_path / "i.sqlite3"
    p.write_bytes(b"this is not a sqlite database at all" * 50)
    with pytest.raises(CorruptIndexError, match="re-index"):
        Store(p)


def test_interrupted_job_is_marked_on_reopen(tmp_path):
    p = tmp_path / "i.sqlite3"
    s = Store(p)
    r = s.add_repository("/tmp/x", "x")["id"]
    s.create_job(r, "full"); s.set_repo_status(r, "indexing")
    s.close()
    s2 = Store(p)
    assert s2.latest_job(r)["status"] == "interrupted"
    assert s2.get_repository(r)["status"] == "interrupted"
    s2.close()


def test_duplicate_repository_rejected(store):
    with pytest.raises(RepositoryError) as ei:
        store.add_repository("/tmp/r", "again")
    assert ei.value.code == "already_registered"


def test_model_mismatch_blocks_mixing_vectors(store):
    store.check_model("m1", 4, adopt=True)
    store.check_model("m1", 4, adopt=False)
    with pytest.raises(ModelMismatchError, match="Re-index"):
        store.check_model("m2", 4, adopt=False)
    with pytest.raises(ModelMismatchError):
        store.check_model("m1", 8, adopt=False)


def test_replace_file_is_atomic_on_failure(store):
    r = rid(store)
    store.replace_file(r, info(), [mk_chunk(chunk_id="c1")], vec())
    before = store.repo_stats(r)["chunks"]
    bad = [mk_chunk(chunk_id="c2"), mk_chunk(chunk_id="c2")]             # duplicate id -> IntegrityError mid-transaction
    with pytest.raises(sqlite3.IntegrityError):
        store.replace_file(r, info(), bad, vec(2))
    assert store.repo_stats(r)["chunks"] == before
    assert store.get_chunk_by_chunk_id("c1") is not None                  # old data intact
    assert store.fts_search(["gamma"], [r], None, 5)                       # FTS untouched too


def test_replace_delete_keep_fts_in_sync(store):
    r = rid(store)
    store.replace_file(r, info(), [mk_chunk(content="def alpha():\n  return refresh", chunk_id="c1")], vec())
    assert store.fts_search(["refresh"], [r], None, 5)
    store.replace_file(r, info(), [mk_chunk(content="def beta():\n  return other", chunk_id="c2")], vec())
    assert not store.fts_search(["refresh"], [r], None, 5) and store.fts_search(["other"], [r], None, 5)
    store.delete_file(r, "a.py")
    assert not store.fts_search(["other"], [r], None, 5) and store.repo_stats(r)["chunks"] == 0


def test_remove_repository_removes_everything(store):
    r = rid(store)
    store.replace_file(r, info(), [mk_chunk(chunk_id="c1")], vec())
    store.remove_repository(r)
    n = store.conn().execute("SELECT (SELECT COUNT(*) FROM chunks)+(SELECT COUNT(*) FROM files)+(SELECT COUNT(*) FROM chunks_fts)").fetchone()[0]
    assert n == 0 and store.list_repositories() == []


def test_symbol_search_tiers_and_wildcard_escaping(store):
    r = rid(store)
    names = ["refresh", "Svc.refresh", "refresh_all", "do_refresh", "plain"]
    for i, n in enumerate(names):
        store.replace_file(r, info(f"f{i}.py"), [mk_chunk(f"f{i}.py", n, chunk_id=f"c{i}")], vec())
    tiers = {store.get_chunks([cid])[cid]["symbol_name"]: t for cid, t in store.symbol_search("refresh", [r], None, 10)}
    assert tiers == {"refresh": 0, "Svc.refresh": 1, "refresh_all": 2, "do_refresh": 3}
    assert store.symbol_search("%", [r], None, 10) == []                    # LIKE wildcards are escaped


def test_reusable_embeddings(store):
    r = rid(store)
    c = mk_chunk(chunk_id="c1")
    store.replace_file(r, info(), [c], vec())
    assert c.embed_hash in store.reusable_embeddings([c.embed_hash, "nope"])


def test_error_marking_keeps_previous_chunks(store):
    r = rid(store)
    store.replace_file(r, info(), [mk_chunk(chunk_id="c1")], vec())
    store.mark_file_error(r, info(), "embedding down password=hunter2hunter2")
    f = store.get_files(r)["a.py"]
    assert f["status"] == "error" and "hunter2" not in f["error"]        # redacted
    assert store.repo_stats(r)["chunks"] == 1
