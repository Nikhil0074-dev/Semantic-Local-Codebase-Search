"""Vector retrieval backend interface + the MVP implementation (exact brute-force cosine over a cached numpy matrix).

At ~100k chunks x 1024 dims the float32 matrix is ~400 MB and a query is a single matrix-vector product, which is why
an approximate-nearest-neighbour database is deferred until benchmarks show it is needed (docs/planning section 16/21).
Replace `NumpyBruteForce` with e.g. a LanceDB adapter implementing the same `VectorBackend` protocol.
"""
from __future__ import annotations

from typing import Optional, Protocol, Sequence

import numpy as np

from .store import Store


class VectorBackend(Protocol):
    def query(self, vector: np.ndarray, repo_ids: Sequence[str], languages: Optional[Sequence[str]], top_k: int) \
            -> list[tuple[int, float]]: ...


class NumpyBruteForce:
    def __init__(self, store: Store, dimension: int):
        self.store, self.dimension = store, dimension
        self._cache: dict[str, tuple[int, np.ndarray, np.ndarray, np.ndarray]] = {}

    def _load(self, rid: str):
        gen = self.store.generation(rid)
        hit = self._cache.get(rid)
        if hit and hit[0] == gen:
            return hit
        cids, langs, rows = [], [], []
        for r in self.store.iter_vectors(rid):
            vec = np.frombuffer(r["embedding"], dtype=np.float32)
            if vec.shape[0] != self.dimension:
                continue  # defensive: never mix dimensions
            cids.append(r["cid"])
            langs.append(r["language"])
            rows.append(vec)
        mat = np.vstack(rows) if rows else np.zeros((0, self.dimension), dtype=np.float32)
        entry = (gen, np.asarray(cids, dtype=np.int64), mat, np.asarray(langs, dtype=object))
        self._cache[rid] = entry
        return entry

    def query(self, vector, repo_ids, languages, top_k):
        vec = np.asarray(vector, dtype=np.float32).reshape(-1)
        results: list[tuple[int, float]] = []
        for rid in repo_ids:
            _, cids, mat, langs = self._load(rid)
            if len(cids) == 0:
                continue
            sims = mat @ vec           # vectors are L2-normalised, so dot product == cosine similarity
            if languages:
                mask = np.isin(langs, list(languages))
                sims = np.where(mask, sims, -np.inf)
            k = min(top_k, len(sims))
            idx = np.argpartition(-sims, k - 1)[:k]
            for i in idx:
                if np.isfinite(sims[i]):
                    results.append((int(cids[i]), float(sims[i])))
        results.sort(key=lambda t: -t[1])
        return results[:top_k]
