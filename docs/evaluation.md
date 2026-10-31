# Evaluation

All numbers below are **measured by code in this repository** and reproducible. They are small-sample, single-repository
results and must not be read as general claims about semantic code search.

## 1. Retrieval benchmark

* Corpus: `sample-repositories/acme-shop` — 20 indexed files (Python, TypeScript, Go, Java, SQL, YAML, Rust), 78 chunks.
* Queries: 20, written by hand **before the first run** (`indexing-engine/tests/retrieval/benchmark_acme_shop.json`);
  3 identifier queries, 5 deliberate vocabulary-mismatch queries, 12 natural-language questions (including the brief's
  example questions that map onto this repo). Relevance is graded (2 = primary answer, 1 = useful supporting code) and matched on
  `(file, symbol)`.
* Metrics: Recall@K, Precision@K, MRR, nDCG@K (graded gain), mean latency.
* Reproduce: `python scripts/run_benchmark.py` (writes `docs/evaluation_results.json`).

| System | Recall@5 | Precision@5 | MRR | nDCG@5 | Recall@10 | nDCG@10 | Hit@10 | mean ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical (BM25) | 0.84 | 0.28 | 0.80 | 0.77 | 0.93 | 0.80 | 1.00 | 2.2 |
| symbol only | 0.15 | 0.04 | 0.15 | 0.15 | 0.15 | 0.15 | 0.15 | 0.2 |
| semantic (baseline embedder) | 0.77 | 0.24 | 0.85 | 0.76 | 0.87 | 0.81 | 0.95 | 1.8 |
| **hybrid (RRF)** | 0.80 | 0.26 | 0.83 | 0.74 | 0.86 | 0.78 | 0.95 | 2.5 |
| semantic, concept lexicon disabled | 0.78 | 0.25 | 0.78 | 0.71 | 0.86 | 0.75 | 0.95 | 1.6 |
| hybrid, concept lexicon disabled | 0.81 | 0.27 | 0.83 | 0.75 | 0.90 | 0.79 | 0.95 | 2.5 |

(Precision@5 is capped by design: most queries have only 1–4 relevant items.)

## 2. What these results do and do not show

* **Keyword search is a strong baseline.** BM25 alone matches or beats hybrid on Recall@5, nDCG and Hit@10; hybrid has a
  slightly higher MRR (0.83 vs 0.80). With 20 queries these differences are within noise.
* **The offline baseline embedder shows no demonstrated semantic advantage.** The hand-written synonym lexicon did not help
  (hybrid without it is equal or marginally better). The baseline is not a neural model; the claim "semantic search improves
  retrieval" is therefore **untested**, not refuted. The benchmark harness is the instrument for testing Ollama models.
* **Symbol-only is useless for natural language** (as expected) and perfect where it applies: for the 3 identifier queries
  the first hybrid result is relevant every time.
* **Misses:** the only query without a relevant result in the top 10 for semantic/hybrid was *"How does the frontend
  communicate with the backend?"*; lexical retrieved it.
* A directly observed weak case: for *"refresh an expiring token"* the best-matching function `renew_credentials` ranks 7th
  because `refresh_tokens`, `TokenStore.*` and `refresh_endpoint` share literal words with the query.

### Threats to validity
Tiny corpus (78 chunks) and 20 queries; queries and ground truth written by the system's author; the concept lexicon was
written with these domains in mind; RRF weights are hand-set and untuned; one repository, mostly Python; no inter-rater
check on relevance labels. A fair test needs several real repositories, independently written queries, and neural models.

## 3. Indexing and query performance (synthetic)

Environment: Linux x86_64 sandbox, Python 3.12.3. Synthetic templated Python repositories, offline embedder.
Reproduce: `python scripts/scale_test.py 200 1000 3000`. `peak_rss` is a process-wide maximum (later rows conservative).

| Files | Chunks | Full index | No-op incremental | 1-file edit | Query median / p95 | Index size | Peak RSS |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 200 | 1 613 | 0.80 s | 0.01 s | 0.01 s | 5.0 / 7.3 ms | 9 MB | 62 MB |
| 1 000 | 8 001 | 4.39 s | 0.02 s | 0.02 s | 8.5 / 21.9 ms | 45 MB | 120 MB |
| 3 000 | 24 108 | 14.66 s | 0.09 s | 0.11 s | 22.8 / 49.8 ms | 136 MB | 263 MB |

Not measured: real-world repositories, neural embedding latency (would dominate indexing time), Windows/macOS, >24k chunks.
Revisit brute-force vectors (add an ANN backend) if p95 query latency exceeds ~100 ms or chunks exceed ~200k.

## 4. Next experiments (in priority order)
1. Install Ollama; run the same benchmark with `nomic-embed-text`, `mxbai-embed-large`, `all-minilm`; report recall/MRR/nDCG and indexing time.
2. Build a larger benchmark (≥3 real repositories, ≥100 queries, independently written, two raters).
3. Ablate chunking (symbol vs fixed-size) and RRF weights on a held-out split.
4. Time-to-locate user study versus IDE search / ripgrep.
