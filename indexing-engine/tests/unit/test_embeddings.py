import numpy as np
import pytest

from slcs.embeddings import HashingEmbedder, OllamaEmbedder
from slcs.errors import EmbeddingError, PathSecurityError


def cos(a, b):
    return float(a @ b)


def test_hash_embedder_is_deterministic_and_normalised():
    e = HashingEmbedder()
    a = e.embed(["def refresh_token(): pass"])
    b = e.embed(["def refresh_token(): pass"])
    assert np.array_equal(a, b) and abs(np.linalg.norm(a[0]) - 1) < 1e-5 and a.shape == (1, e.dimension)
    assert e.embed([]).shape == (0, e.dimension)


def test_concept_lexicon_links_synonyms_but_not_unrelated_text():
    e = HashingEmbedder()
    q = e.embed(["refresh the token"], "query")[0]
    near = e.embed(["def renew_credentials(): ..."], "query")[0]
    far = e.embed(["def draw_chart(): ..."], "query")[0]
    assert cos(q, near) > cos(q, far) + 0.1
    plain = HashingEmbedder(use_concepts=False)
    assert plain.model_id != e.model_id
    assert cos(plain.embed(["refresh the token"], "query")[0], plain.embed(["renew credentials"], "query")[0]) < 0.1


def test_ollama_success_prefixes_batches_and_normalises(ollama):
    e = OllamaEmbedder(ollama.url, "nomic-embed-text", doc_prefix="search_document: ", batch_size=2)
    out = e.embed(["a", "b", "c"], "document")
    assert out.shape == (3, 8) and np.allclose(np.linalg.norm(out, axis=1), 1, atol=1e-5)
    assert len(ollama.calls) == 2                                  # 3 texts in batches of 2
    assert ollama.calls[0]["input"][0] == "search_document: a" and e.dimension == 8


def test_ollama_query_prefix(ollama):
    e = OllamaEmbedder(ollama.url, "m", query_prefix="search_query: ")
    e.embed(["hello"], "query")
    assert ollama.calls[-1]["input"] == ["search_query: hello"]


def test_ollama_unreachable_raises_embedding_error():
    e = OllamaEmbedder("http://127.0.0.1:9", "m", retries=0, timeout=1)
    with pytest.raises(EmbeddingError, match="Cannot reach Ollama"):
        e.embed(["x"])


def test_ollama_missing_model_has_actionable_message(ollama):
    ollama.state["mode"] = "missing"
    with pytest.raises(EmbeddingError) as ei:
        OllamaEmbedder(ollama.url, "nomic-embed-text", retries=0).embed(["x"])
    assert ei.value.code == "model_missing" and "ollama pull" in str(ei.value)


def test_ollama_server_error_and_garbage_and_bad_count(ollama):
    for mode in ("http500", "garbage", "bad_count"):
        ollama.state["mode"] = mode
        with pytest.raises(EmbeddingError):
            OllamaEmbedder(ollama.url, "m", retries=0).embed(["a", "b"])


def test_ollama_dimension_change_is_flagged(ollama):
    e = OllamaEmbedder(ollama.url, "m")
    e.embed(["a"])
    ollama.state["dim"] = 16
    with pytest.raises(EmbeddingError) as ei:
        e.embed(["b"])
    assert ei.value.code == "model_mismatch"


def test_remote_embedding_endpoint_blocked_by_default():
    with pytest.raises(PathSecurityError) as ei:
        OllamaEmbedder("http://example.com:11434", "m")
    assert ei.value.code == "remote_embedding_blocked"
    OllamaEmbedder("http://example.com:11434", "m", allow_remote=True)      # explicit opt-in works
    with pytest.raises(PathSecurityError):
        OllamaEmbedder("file:///etc/passwd", "m")


def test_ollama_status_reports_installed_and_missing(ollama):
    assert OllamaEmbedder(ollama.url, "nomic-embed-text").status()["available"] is True
    st = OllamaEmbedder(ollama.url, "other-model").status()
    assert st["available"] is False and "ollama pull other-model" in st["error"]


def test_connection_refused_fails_fast_without_retry_backoff():
    import time
    e = OllamaEmbedder("http://127.0.0.1:9", "m", retries=3, timeout=5)
    t = time.perf_counter()
    with pytest.raises(EmbeddingError):
        e.embed(["x"])
    assert time.perf_counter() - t < 0.5          # no pointless backoff when nothing is listening
