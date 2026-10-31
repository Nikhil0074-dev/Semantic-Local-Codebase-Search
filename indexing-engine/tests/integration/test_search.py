import json
import os
import shutil

import pytest

from slcs.errors import EmbeddingError, QueryError, RepositoryError
from slcs.search import classify_query


def top(svc, q, rid, n=5, **kw):
    return [(r["relative_path"], r["symbol_name"]) for r in svc.search(q, [rid], limit=n, **kw)["results"]]


def test_query_classification():
    assert classify_query("refreshAccessToken") == "identifier"
    assert classify_query("Class.method") == "identifier"
    assert classify_query("S3_BUCKET_NAME") == "identifier"
    assert classify_query("/api/auth/refresh") == "identifier"
    assert classify_query('"charge.succeeded"') == "literal"
    assert classify_query("where is the token refreshed") == "natural"


def test_exact_symbol_is_ranked_first_with_high_confidence(indexed):
    svc, rid, _ = indexed
    res = svc.search("refreshAccessToken", [rid])["results"]
    assert res[0]["symbol_name"] == "refreshAccessToken" and res[0]["confidence"] == "high"
    assert res[0]["scores"]["symbol_tier"] == 0 and any("exactly matches" in r for r in res[0]["reasons"])


def test_class_method_member_lookup(indexed):
    svc, rid, _ = indexed
    assert ("services/inventory/stock.go", "StockService.ReserveStock") in top(svc, "ReserveStock", rid, 3)


def test_env_var_and_route_literal_search(indexed):
    svc, rid, _ = indexed
    assert ("backend/config/storage.py", "load_storage_settings") in top(svc, "S3_BUCKET_NAME", rid, 3)
    assert ("backend/api/routes.py", "refresh_endpoint") in top(svc, "/api/auth/refresh", rid, 5)
    assert top(svc, '"charge.succeeded"', rid, 2)[0][0].endswith("PaymentWebhookHandler.java")


def test_natural_language_finds_relevant_code(indexed):
    svc, rid, _ = indexed
    assert ("backend/db/connection.py", "init_database_pool") in top(svc, "Where is the database connection initialized?", rid, 3)
    assert ("backend/logging_utils.py", "log_api_error") in top(svc, "Where are API errors logged?", rid, 3)


def test_vocabulary_mismatch_is_bridged_by_semantic_signal(indexed):
    svc, rid, _ = indexed
    # Honest expectation for the offline baseline: found, but NOT at the top. Measured rank is 7 because
    # refresh_tokens / TokenStore / refresh_endpoint share literal words with the query (see docs/evaluation.md).
    res = top(svc, "refresh an expiring token", rid, 10)
    assert ("backend/auth/token_service.py", "renew_credentials") in res


def test_modes_are_distinct(indexed):
    svc, rid, _ = indexed
    r = svc.search("how are passwords stored", [rid], mode="lexical")["results"]
    assert all(x["scores"]["semantic"] is None for x in r)
    r = svc.search("how are passwords stored", [rid], mode="semantic")["results"]
    assert all(x["scores"]["bm25"] is None for x in r)


def test_validation_errors(indexed):
    svc, rid, _ = indexed
    with pytest.raises(QueryError) as e: svc.search("   ", [rid])
    assert e.value.code == "empty_query"
    with pytest.raises(QueryError) as e: svc.search("x" * 2001, [rid])
    assert e.value.code == "query_too_long"
    with pytest.raises(QueryError): svc.search("ok", [rid], limit=0)
    with pytest.raises(QueryError): svc.search("ok", [rid], limit=True)
    with pytest.raises(QueryError): svc.search("ok", [rid], mode="magic")
    with pytest.raises(QueryError): svc.search("ok", [rid], languages="python")
    with pytest.raises(QueryError): svc.search(123, [rid])
    with pytest.raises(RepositoryError): svc.search("ok", ["repo_nope"])


def test_control_characters_are_stripped(indexed):
    svc, rid, _ = indexed
    assert svc.search("refresh\x00Access\x07Token", [rid])["query"] == "refreshAccessToken"


def test_language_filter(indexed):
    svc, rid, _ = indexed
    res = svc.search("payment gateway", [rid], languages=["java"], limit=10)["results"]
    assert res and {r["language"] for r in res} == {"java"}
    assert svc.search("payment gateway", [rid], languages=["rust"])["results"] == [] or \
        {r["language"] for r in svc.search("payment gateway", [rid], languages=["rust"])["results"]} == {"rust"}


def test_no_repositories_and_empty_results_messages(svc):
    r = svc.search("anything")
    assert r["results"] == [] and "No indexed repositories" in r["message"]


def test_nonsense_query_returns_no_confident_results(indexed):
    svc, rid, _ = indexed
    r = svc.search("zxqv wlkj qqqq", [rid])
    assert r["results"] == [] and "No matching" in r["message"]


def test_low_confidence_is_reported(indexed):
    svc, rid, _ = indexed
    r = svc.search("pbkdf2 iterations", [rid], mode="lexical")
    assert r["results"]                                 # found by a single signal only
    assert r["low_confidence"] is True or r["results"][0]["confidence"] != "high"


def test_duplicate_content_is_collapsed(indexed):
    svc, rid, repo = indexed
    shutil.copy(repo / "backend/auth/passwords.py", repo / "backend/auth/passwords_copy.py")
    svc.index_blocking(rid)
    hits = [r for r in svc.search("hash_password", [rid], limit=10)["results"] if r["symbol_name"] == "hash_password"]
    assert len(hits) == 1 and hits[0]["duplicates"] == 1


def test_freshness_fresh_stale_missing(indexed):
    svc, rid, repo = indexed
    assert svc.search("hash_password", [rid])["results"][0]["freshness"] == "fresh"
    p = repo / "backend/auth/passwords.py"
    original = p.read_text()
    p.write_text("# new first line\n" + original.replace("200_000", "300_000"))
    r = svc.search("hash_password", [rid], mode="symbol")["results"][0]
    assert r["freshness"] == "stale" and any("changed since indexing" in x for x in r["reasons"])
    p.write_text("# shifted\n" + original)       # same function text, but line numbers moved by one
    r2 = svc.search("check_password", [rid], mode="symbol")["results"][0]
    assert r2["freshness"] == "stale"            # the indexed line range no longer matches the same text
    p.unlink()
    assert svc.search("hash_password", [rid], mode="symbol")["results"][0]["freshness"] == "missing"


def test_semantic_degrades_gracefully_when_embedder_down(settings, repo_copy, flaky):
    from slcs.service import AppService
    svc = AppService(settings, embedder=flaky)
    rid = svc.register_repository(str(repo_copy))["id"]
    svc.index_blocking(rid)
    flaky.fail = True
    r = svc.search("where are errors logged", [rid])
    assert r["semantic_available"] is False and r["warnings"] and r["results"]    # keyword results still returned
    with pytest.raises(EmbeddingError):
        svc.search("where are errors logged", [rid], mode="semantic")


def test_results_never_leak_absolute_paths(indexed):
    svc, rid, repo = indexed
    blob = json.dumps(svc.search("payment gateway", [rid])) + json.dumps(svc.diagnostics())
    assert str(repo) not in blob


def test_multi_repository_search_and_filter(indexed, tmp_path):
    svc, rid, _ = indexed
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.py").write_text("def refresh_everything():\n    return 'other_repo'\n")
    rid2 = svc.register_repository(str(other))["id"]
    svc.index_blocking(rid2)
    both = svc.search("refresh_everything")["results"]
    assert both[0]["repository_id"] == rid2
    assert not svc.search("refresh_everything", [rid], mode="symbol")["results"]


def test_result_contract_fields(indexed):
    svc, rid, _ = indexed
    r = svc.search("log_api_error", [rid])["results"][0]
    for key in ("chunk_id", "repository_id", "repository_name", "relative_path", "symbol_name", "language", "start_line",
                "end_line", "snippet", "scores", "freshness", "confidence", "reasons"):
        assert key in r
    assert not os.path.isabs(r["relative_path"]) and r["start_line"] <= r["end_line"]
