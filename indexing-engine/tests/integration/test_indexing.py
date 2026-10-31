import threading
import time

import pytest

from slcs.config import Settings
from slcs.errors import ModelMismatchError, RepositoryUnavailable
from slcs.service import AppService
from slcs.store import Store


def test_initial_index_populates_files_chunks_and_skips(indexed):
    svc, rid, _ = indexed
    st = svc.repository_status(rid)
    assert st["status"] == "indexed" and st["stats"]["files"] >= 20 and st["stats"]["chunks"] > 40
    reasons = {s["rel_path"]: s["reason"] for s in st["skipped"]}
    assert reasons[".env"] == "sensitive" and reasons["generated/api_pb.py"] == "generated"
    assert reasons["dist/"] == "excluded_dir"
    assert reasons["node_modules/"] == "excluded_dir" and reasons["scratch/"] == "gitignored"
    assert st["stats"]["languages"]["python"] >= 8


def test_second_run_is_a_no_op(indexed):
    svc, rid, _ = indexed
    j = svc.index_blocking(rid)
    assert j.files_indexed == 0 and j.files_unchanged > 0 and j.chunks_written == 0


def test_edit_reindexes_only_that_file_and_reuses_unchanged_chunk_vectors(indexed):
    svc, rid, repo = indexed
    p = repo / "backend/logging_utils.py"
    p.write_text(p.read_text().replace('level: str = "INFO"', 'level: str = "DEBUG"'))
    j = svc.index_blocking(rid)
    assert j.files_indexed == 1 and j.chunks_reused >= 1 and j.chunks_written >= 1
    hits = svc.search("configure_logging", [rid], mode="symbol")["results"]
    assert "DEBUG" in hits[0]["snippet"]


def test_touch_without_content_change_does_not_reindex(indexed):
    svc, rid, repo = indexed
    p = repo / "backend/logging_utils.py"
    p.write_text(p.read_text())          # new mtime, same bytes
    time.sleep(0.01)
    j = svc.index_blocking(rid)
    assert j.files_indexed == 0


def test_new_and_deleted_files(indexed):
    svc, rid, repo = indexed
    (repo / "backend" / "new_feature.py").write_text("def brand_new_feature():\n    return 'unique_zebra_marker'\n")
    (repo / "backend/logging_utils.py").unlink()
    j = svc.index_blocking(rid)
    assert j.files_indexed == 1 and j.files_removed == 1
    assert svc.search("brand_new_feature", [rid], mode="symbol")["results"]
    assert not [r for r in svc.search("log_api_error", [rid], mode="symbol")["results"]
                if r["relative_path"] == "backend/logging_utils.py"]       # definition gone (usages elsewhere remain)


def test_file_becoming_excluded_is_removed(indexed):
    svc, rid, repo = indexed
    (repo / ".gitignore").write_text("*.log\nscratch/\nservices/\n")
    j = svc.index_blocking(rid)
    assert j.files_removed >= 4
    assert not svc.search("ReserveStock", [rid], mode="symbol")["results"]


def test_rename_moves_the_index_entry(indexed):
    svc, rid, repo = indexed
    (repo / "backend/logging_utils.py").rename(repo / "backend/log_helpers.py")
    svc.index_blocking(rid)
    r = svc.search("log_api_error", [rid], mode="symbol")["results"]
    assert r[0]["relative_path"] == "backend/log_helpers.py"


def test_embedding_outage_preserves_old_data_and_retries_later(settings, repo_copy, flaky):
    svc = AppService(settings, embedder=flaky)
    rid = svc.register_repository(str(repo_copy))["id"]
    svc.index_blocking(rid)
    before = svc.repository_status(rid)["stats"]["chunks"]
    p = repo_copy / "backend/auth/passwords.py"
    p.write_text(p.read_text() + "\n\ndef extra_helper():\n    return 'xylophone'\n")
    flaky.fail = True
    j = svc.index_blocking(rid)
    st = svc.repository_status(rid)
    assert j.files_failed == 1 and st["status"] == "indexed_with_errors"
    assert st["stats"]["chunks"] == before                                    # nothing lost
    assert st["file_errors"][0]["rel_path"] == "backend/auth/passwords.py"
    assert svc.search("hash_password", [rid], mode="symbol")["results"]       # old content still searchable
    flaky.fail = False
    j = svc.index_blocking(rid)                                                # failed file is retried automatically
    assert j.files_failed == 0 and svc.search("extra_helper", [rid], mode="symbol")["results"]
    assert svc.repository_status(rid)["status"] == "indexed"


def test_new_file_failing_to_embed_is_recorded_not_dropped_silently(settings, repo_copy, flaky):
    svc = AppService(settings, embedder=flaky)
    rid = svc.register_repository(str(repo_copy))["id"]
    flaky.fail = True
    j = svc.index_blocking(rid)
    assert j.files_failed >= 20                      # every file that has chunks to embed fails; none is silently dropped
    assert len(svc.repository_status(rid)["file_errors"]) == j.files_failed


def test_model_change_requires_explicit_reindex(settings, repo_copy):
    from slcs.embeddings import HashingEmbedder
    svc = AppService(settings)
    rid = svc.register_repository(str(repo_copy))["id"]
    svc.index_blocking(rid)
    svc2 = AppService(settings, embedder=HashingEmbedder(use_concepts=False))
    with pytest.raises(ModelMismatchError):
        svc2.index_blocking(rid)
    svc2.reindex_all_for_current_model()
    assert svc2.repository_status(rid)["status"] == "needs_reindex" and svc2.repository_status(rid)["stats"]["chunks"] == 0
    svc2.index_blocking(rid)
    assert svc2.repository_status(rid)["stats"]["chunks"] > 0
    assert svc2.model_status()["compatible"] is True


def test_missing_repository_folder_preserves_index(indexed):
    svc, rid, repo = indexed
    import shutil
    before = svc.repository_status(rid)["stats"]["chunks"]
    moved = repo.parent / "moved"
    repo.rename(moved)
    with pytest.raises(RepositoryUnavailable):
        svc.index_blocking(rid)
    st = svc.repository_status(rid)
    assert st["status"] == "unavailable" and st["available"] is False and st["stats"]["chunks"] == before
    svc.relocate_repository(rid, str(moved))                                   # relocation keeps the index
    assert svc.index_blocking(rid).files_failed == 0


def test_cancel_stops_indexing(settings, repo_copy):
    svc = AppService(settings)
    rid = svc.register_repository(str(repo_copy))["id"]
    ev = threading.Event(); ev.set()
    j = svc.indexer.index_repository(rid, "full", cancel=ev)
    assert j.status == "cancelled" and svc.repository_status(rid)["status"] == "cancelled"
    assert svc.index_blocking(rid).status == "completed"                       # can resume afterwards


def test_interrupted_run_is_consistent_and_resumes(settings, repo_copy):
    svc = AppService(settings)
    rid = svc.register_repository(str(repo_copy))["id"]
    calls = {"n": 0}
    orig = svc.store.replace_file

    def crash_after_five(*a, **k):
        calls["n"] += 1
        if calls["n"] > 5:
            raise KeyboardInterrupt("simulated kill")
        return orig(*a, **k)

    svc.store.replace_file = crash_after_five
    with pytest.raises(KeyboardInterrupt):
        svc.indexer.index_repository(rid)
    svc.store.close()
    store2 = Store(settings.db_path)                                           # "restart"
    assert store2.latest_job(rid)["status"] == "interrupted"
    assert store2.repo_stats(rid)["files"] == 5
    store2.close()
    svc2 = AppService(settings)
    j = svc2.index_blocking(rid)
    assert j.files_indexed > 0 and j.files_failed == 0
    full = svc2.repository_status(rid)["stats"]["files"]
    assert full > 5


def test_background_job_and_progress(settings, repo_copy):
    svc = AppService(settings)
    rid = svc.register_repository(str(repo_copy))["id"]
    svc.start_index(rid)
    svc.wait(rid)
    st = svc.repository_status(rid)
    assert st["status"] == "indexed" and st["job"]["status"] == "completed" and not st["running"]


def test_removing_repository_deletes_index_but_never_source(indexed):
    svc, rid, repo = indexed
    n_files = sum(1 for _ in repo.rglob("*") if _.is_file())
    svc.remove_repository(rid)
    assert svc.list_repositories() == []
    assert sum(1 for _ in repo.rglob("*") if _.is_file()) == n_files


def test_malformed_python_is_indexed_with_warning(indexed):
    svc, rid, repo = indexed
    (repo / "backend" / "broken.py").write_text("def oops(:\n    this is not python ###\nSOME_CONSTANT = 'findable_marker'\n")
    svc.index_blocking(rid)
    st = svc.repository_status(rid)
    assert any(w["rel_path"] == "backend/broken.py" for w in st["warnings"])
    assert svc.search("findable_marker", [rid], mode="symbol")["results"]       # literal search still finds it
