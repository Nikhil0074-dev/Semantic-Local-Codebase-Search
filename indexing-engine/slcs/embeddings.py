"""Embedding providers.

HashingEmbedder: deterministic, dependency-free, fully offline. It is a *baseline* (feature hashing over code
sub-words, bigrams and a small general software-vocabulary concept lexicon). It is NOT a neural model and will not
match the semantic quality of a real code embedding model - use the Ollama provider for that.

OllamaEmbedder: calls the local Ollama HTTP API (POST /api/embed). Endpoint must be loopback unless the user opts in.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from functools import lru_cache
from typing import Protocol

import numpy as np

from .errors import EmbeddingError
from .security import assert_local_url
from .text import split_words, stem, STOPWORDS


class Embedder(Protocol):
    model_id: str
    dimension: int

    def embed(self, texts: list[str], kind: str = "document") -> np.ndarray: ...
    def status(self) -> dict: ...


def _l2(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


# ------------------------------------------------------------------------------------------------ hashing baseline
CONCEPTS: dict[str, str] = {
    "auth": "authenticate authentication login signin credential session jwt bearer oauth identity sso",
    "token": "token jwt bearer credential access refresh",
    "renew": "refresh renew renewal reissue rotate rotation extend prolong revalidate",
    "expire": "expire expired expiry expiration lapse lapsed stale outdated timeout ttl",
    "permission": "permission authorize authorization authorise allow allowed role rbac acl privilege policy forbidden "
                  "grant deny denied entitlement",
    "validate": "validate validation verify check ensure guard enforce require",
    "update": "update modify edit change mutate patch save",
    "delete": "delete remove drop destroy purge erase",
    "database": "database db sql sqlite postgres mysql pool datastore persistence repository table query",
    "init": "init initialize initialise setup bootstrap configure create open start",
    "transaction": "transaction commit rollback atomic",
    "fail": "fail failure failed error exception raise throw crash fault abort",
    "log": "log logging logger record audit trace report",
    "payment": "payment pay charge billing invoice checkout card refund gateway merchant",
    "http": "http https request response fetch api client endpoint rest axios url route",
    "config": "config configuration settings setting environment env option preference",
    "storage": "storage bucket blob s3 cloud upload",
    "email": "email mail smtp notification notify send",
    "inventory": "inventory stock warehouse reserve reservation sku",
    "retry": "retry backoff reattempt attempt",
    "secret": "password passphrase hash bcrypt argon scrypt salt encrypt digest",
    "user": "user account customer member",
    "order": "order purchase cart",
}


@lru_cache(maxsize=1)
def _concept_index() -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for concept, words in CONCEPTS.items():
        for w in words.split():
            idx.setdefault(stem(w), []).append("c_" + concept)
    return idx


@lru_cache(maxsize=262144)
def _slot(token: str, dim: int) -> tuple[int, float]:
    h = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "little")
    return h % dim, (1.0 if (h >> 63) & 1 else -1.0)


class HashingEmbedder:
    model_id = "slcs-hash-v1"

    def __init__(self, dimension: int = 1024, use_concepts: bool = True):
        self.dimension = dimension
        self.use_concepts = use_concepts
        if not use_concepts:
            self.model_id = "slcs-hash-v1-noconcepts"

    def status(self) -> dict:
        return {"provider": "hash", "model": self.model_id, "dimension": self.dimension, "available": True,
                "offline": True, "note": "Deterministic baseline; install Ollama + an embedding model for neural search."}

    def _vector(self, text: str, header_lines: int) -> np.ndarray:
        feats: dict[str, float] = {}
        concepts = _concept_index() if self.use_concepts else {}
        for li, line in enumerate(text.split("\n")):
            boost = 2.0 if li < header_lines else 1.0
            words = [stem(w) for w in split_words(line) if len(w) > 1 and w not in STOPWORDS]
            prev = None
            for w in words:
                feats[w] = feats.get(w, 0.0) + boost
                for c in concepts.get(w, ()):
                    feats[c] = feats.get(c, 0.0) + 1.2 * boost
                if prev is not None:
                    bg = prev + "_" + w
                    feats[bg] = feats.get(bg, 0.0) + 0.4 * boost
                prev = w
        vec = np.zeros(self.dimension, dtype=np.float32)
        for tok, cnt in feats.items():
            i, sign = _slot(tok, self.dimension)
            vec[i] += sign * (1.0 + math.log(cnt))
        return vec

    def embed(self, texts: list[str], kind: str = "document") -> np.ndarray:
        header = 2 if kind == "document" else 0
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        return _l2(np.stack([self._vector(t, header) for t in texts]))


# ------------------------------------------------------------------------------------------------ Ollama
class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, *, doc_prefix: str = "", query_prefix: str = "",
                 timeout: float = 60.0, allow_remote: bool = False, retries: int = 2, batch_size: int = 32):
        assert_local_url(base_url, allow_remote)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.doc_prefix, self.query_prefix = doc_prefix, query_prefix
        self.timeout, self.retries, self.batch_size = timeout, retries, batch_size
        self.model_id = f"ollama:{model}"
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = int(self.embed(["dimension probe"], "query").shape[1])
        return self._dimension

    def _request(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json"})
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:200]
                if e.code == 404:
                    raise EmbeddingError(f"Ollama model '{self.model}' not found. Run: ollama pull {self.model}",
                                         code="model_missing") from e
                last = EmbeddingError(f"Ollama returned HTTP {e.code}: {body}")
                if e.code < 500:
                    raise last from e
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = EmbeddingError(f"Cannot reach Ollama at {self.base_url} ({type(e).__name__}). Is it running?")
                reason = getattr(e, "reason", e)
                if isinstance(reason, ConnectionRefusedError):
                    raise last from e      # nothing is listening: retrying only adds latency
            except ValueError as e:
                raise EmbeddingError("Ollama returned invalid JSON") from e
            time.sleep(min(0.2 * (2 ** attempt), 1.0))
        assert last is not None
        raise last

    def status(self) -> dict:
        info = {"provider": "ollama", "model": self.model_id, "available": False, "offline": True,
                "endpoint": self.base_url}
        try:
            tags = self._request("/api/tags")
            names = {m.get("name", "") for m in tags.get("models", [])}
            present = self.model in names or f"{self.model}:latest" in names
            info["available"] = present
            if not present:
                info["error"] = f"model '{self.model}' is not installed (ollama pull {self.model})"
            else:
                info["dimension"] = self._dimension
        except EmbeddingError as e:
            info["error"] = str(e)
        return info

    def embed(self, texts: list[str], kind: str = "document") -> np.ndarray:
        prefix = self.doc_prefix if kind == "document" else self.query_prefix
        rows: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [prefix + t for t in texts[i:i + self.batch_size]]
            out = self._request("/api/embed", {"model": self.model, "input": batch, "truncate": True})
            embs = out.get("embeddings")
            if not isinstance(embs, list) or len(embs) != len(batch):
                raise EmbeddingError("Ollama response did not contain one embedding per input")
            rows.extend(embs)
        if not rows:
            return np.zeros((0, self._dimension or 0), dtype=np.float32)
        try:
            mat = np.asarray(rows, dtype=np.float32)
        except ValueError as e:
            raise EmbeddingError("Ollama returned ragged embeddings") from e
        if mat.ndim != 2 or not np.isfinite(mat).all():
            raise EmbeddingError("Ollama returned malformed embeddings")
        if self._dimension is not None and mat.shape[1] != self._dimension:
            raise EmbeddingError(f"Embedding dimension changed ({self._dimension} -> {mat.shape[1]})",
                                 code="model_mismatch")
        self._dimension = int(mat.shape[1])
        return _l2(mat)


def make_embedder(settings) -> "Embedder":
    if settings.embedding_provider == "ollama":
        return OllamaEmbedder(settings.ollama_url, settings.ollama_model, doc_prefix=settings.ollama_doc_prefix,
                              query_prefix=settings.ollama_query_prefix, timeout=settings.embed_timeout_s,
                              allow_remote=settings.allow_remote_embedding, batch_size=settings.embed_batch_size)
    return HashingEmbedder()
