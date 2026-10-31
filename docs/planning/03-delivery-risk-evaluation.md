# Planning Report (Part 3 of 3) — Delivery, Risk, Evaluation

Sections **41–57**. Labels as in Part 1.

## 41. Privacy Model
Default: **repository content, embeddings, queries and results stay on this device.**
| Question | Answer |
|---|---|
| Data collected | None sent anywhere. Locally: index DB (chunks incl. source text, vectors), `settings.json`, optional log |
| Telemetry | None; there is no setting that enables it |
| External contact | Only the embedding endpoint, and only if it resolves to loopback (else blocked unless `allow_remote_embedding`). Baseline embedder: none |
| Model unavailable | keyword/symbol search continues with a warning |
| Deletion | Remove repository → index rows deleted; source never modified. Delete the data dir to erase everything |
| Logs | no snippets, no query text, secrets redacted, home dir shown as `~` |
| Backups | OS backups of the data dir would include source text from the index — exclude it if that matters |
Install-time vs offline: first use of Ollama requires downloading a model (network). After that, retrieval needs no network. This was **not** verifiable here (no Ollama); the baseline embedder path was verified to issue zero external requests (UI test).

## 42. Testing Strategy and Results
**167 automated tests (166 passed, 1 skipped — a permission test that cannot run as root)** — unit 76, security 21, integration 52, retrieval/metrics 8, browser UI 10. Reproduce: `scripts/run_tests.sh`. Passing tests are evidence, not proof that the system is bug-free; defects found and fixed *during* this build by testing are listed in `docs/testing.md`.

Representative cases (test id → scenario → expected):
| ID | Feature | Scenario | Expected |
|---|---|---|---|
| T-01 | Chunking | every chunk of the sample repo | `content == lines[start-1:end]` |
| T-02 | Scanner | `.env`, `node_modules`, `dist`, generated, gitignored | skipped with reasons |
| T-03 | Scanner | symlink to outside dir/file | not followed, reported `symlink` |
| T-04 | Scanner | file > limit / > file cap | skipped `too_large` / `file_limit` |
| T-05 | Parser | malformed Python | warning + line-window fallback, still searchable |
| T-06 | Parser | braces inside strings/comments | symbol ranges still correct |
| T-07 | Embeddings | Ollama down / 404 / HTTP 500 / garbage / wrong count | `EmbeddingError` with actionable message |
| T-08 | Embeddings | dimension changes mid-run | `model_mismatch` |
| T-09 | Embeddings | remote Ollama URL | blocked unless opted in |
| T-10 | Embeddings | connection refused | fails in < 0.5 s (no retry backoff) |
| T-11 | Store | duplicate chunk id mid-transaction | rollback; old data and FTS intact |
| T-12 | Store | newer schema / corrupt file | `IncompatibleIndexError` / `CorruptIndexError` |
| T-13 | Store | job left `running` then reopen | marked `interrupted`, repo `interrupted` |
| T-14 | Indexing | no-op second run | 0 files re-indexed |
| T-15 | Indexing | edit one function | 1 file re-indexed, other chunk vectors reused |
| T-16 | Indexing | embedder outage | old data kept, file `error`, retried next run |
| T-17 | Indexing | crash after 5 files then restart | 5 files valid; resume completes |
| T-18 | Indexing | model switch | blocked until explicit re-index; vectors cleared only |
| T-19 | Indexing | folder moved | `unavailable`, index preserved; relocate works |
| T-20 | Search | exact symbol / member / env var / route / literal | ranked first, "exactly matches" reason |
| T-21 | Search | empty, 2001 chars, bad limit/mode/languages, unknown repo | typed 400/404 errors |
| T-22 | Search | edit / shift / delete file after indexing | `stale` / `stale` / `missing` |
| T-23 | Search | embedder down | keyword results + warning; `semantic` mode raises |
| T-24 | Search | duplicate content in two files | one result, `duplicates: 1` |
| T-25 | Security | `../`, absolute, UNC, NUL, symlink escape | refused |
| T-26 | Security | redaction (AWS key, JWT, bearer, private key, password=) | redacted |
| T-27 | Server | no/wrong token; foreign Host/Origin; form POST | 401 / 403 / 403 / 415 |
| T-28 | Server | `/api/open` with a path instead of chunk id; traversal id | 400 / 404 |
| T-29 | Server | settings: `data_dir`, `editor_command`, remote URL | 403; rejected settings not persisted |
| T-30 | Navigation | editor missing / unknown / Windows `&` path | actionable error / refused |
| T-31 | UI | page load + search + settings | **zero** external requests, zero console errors |
| T-32 | UI | source containing `<img onerror>` / `<script>` | shown as text, never executed |
| T-33 | UI | narrow 400 px window | no horizontal overflow |
| T-34 | UI | remove repo | confirm dialog; source files intact |
| T-35 | Metrics | known rankings | Recall/MRR/nDCG match hand calculation |

**Not tested:** Windows/macOS execution; a real editor launch; real Ollama; screen readers/axe; files > 1 MB; repositories > 3 000 files for retrieval *quality*; concurrent multi-client use; dependency vulnerability scan.

## 43. Performance Evaluation
Environment: Linux x86_64 sandbox, Python 3.12.3 (CPU model/RAM not recorded). **Synthetic** Python repository (templated functions), offline hashing embedder, single process; `peak_rss` is a process-wide maximum so later rows are conservative. Reproduce: `python scripts/scale_test.py 200 1000 3000`. Not representative of real code or of a neural embedder (whose latency would dominate indexing).
| Files | Chunks | Full index | No-op re-run | 1-file edit | Query median / p95 | DB size | Peak RSS |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 200 | 1 613 | 0.80 s | 0.01 s | 0.01 s | 5.0 / 7.3 ms | 9 MB | 62 MB |
| 1 000 | 8 001 | 4.39 s | 0.02 s | 0.02 s | 8.5 / 21.9 ms | 45 MB | 120 MB |
| 3 000 | 24 108 | 14.66 s | 0.09 s | 0.11 s | 22.8 / 49.8 ms | 136 MB | 263 MB |
Query cost scales ~linearly with chunks (brute-force); extrapolation beyond 24k is **[Assumption]**. Real-repo (e.g. 50k+ files) and neural-embedder timings: **[To validate]**.

## 44. Error Handling and Recovery
| Failure | Behaviour | Recoverable? |
|---|---|---|
| Embedding service down / 5xx | per-file error, old data kept, retried next run; search degrades to keyword | yes |
| Model missing (404) | message "ollama pull <model>" | yes (user action) |
| Dimension/model mismatch | block indexing; search degrades; explicit re-index | yes (user action) |
| Corrupt index / newer schema | clear error; delete & re-index (source untouched) | user action |
| Permission denied / unreadable dir or file | listed in skipped/errors | yes |
| Repository removed/moved | `unavailable`; index preserved; relocate or restore | yes |
| File changed during indexing | next scan hashes it again; staleness check on results | yes |
| Disk full / DB write error | transaction rolls back; job `failed`; prior state intact **[reasoned; not tested]** | user action |
| Process killed | consistent per-file commits; job `interrupted`; resume | yes |
| Unsupported language / parser failure | windows fallback + warning | yes |
| Editor unavailable | actionable message, settings link | yes |
| Unexpected exception in worker | caught, logged by type, shown as `internal_error` | no |

## 45. Observability
Per job: files seen/indexed/unchanged/failed/removed, chunks written/reused, skipped, status, timestamps (table `jobs`). Per repo: file errors, skipped reasons, parser warnings. Per search: timings per retriever, query class, semantic availability. Structured-ish logging via `logging`, level configurable; **no snippets or query text logged**; `GET /api/diagnostics` exports counts only. Distinguishing problems: *parser* → repo "Parser notes"; *model* → model pill + warnings; *corruption* → `corrupt_index`; *config* → settings errors; *search quality* → reasons/signals per result + benchmark.

## 46. Backup, Recovery, Data Lifecycle
| Data | Recoverable? | Notes |
|---|---|---|
| Repository registrations, settings | Keep (small) | `index.sqlite3`, `settings.json` |
| Index (chunks/vectors/FTS) | Rebuildable from source | Safe to delete |
| Search history | not stored | |
| Source text copy | inside index only | repo remains authoritative |
| Logs | stdout/stderr only | |
Complete removal: remove repository in app (or delete the data dir). Index export/transfer: not supported (rebuild instead). Migration: `user_version`; future migrations must be additive or force re-index.

## 47. Deployment and Packaging
Verified: development mode (`pip install -e .`, `slcs serve`). **Not built/verified:** Tauri bundle, PyInstaller sidecar, Windows/macOS installers. Proposed: PyInstaller-frozen `slcs` as a Tauri sidecar; guided first-run to install Ollama + pull a model (not bundled: size and licence); upgrade = replace app, index auto-checked by schema/model version. Windows setup instructions are in `docs/installation.md`. Docker not required.

## 48. Cost and Resource Analysis
Licensing cost: all runtime dependencies are open source **[To validate exact licences before redistribution]**. Cloud cost: none by default. Hardware: baseline embedder runs on any CPU; neural model adds hundreds of MB of disk/RAM **[unmeasured]**. Effort: engine+UI+tests built in this project; remaining effort = packaging, Windows/macOS testing, neural evaluation, Tree-sitter.

## 49. Risk Analysis
| ID | Risk | P | I | Mitigation | Validation |
|---|---|---|---|---|---|
| R-01 | Semantic retrieval no better than BM25 | **High (observed with baseline)** | High | Keep hybrid + honest eval; test neural model on larger corpus | `docs/evaluation.md` rerun with Ollama |
| R-02 | High memory at scale | M | M | brute-force matrix ≈ N·d·4 B; LanceDB adapter trigger | scale test |
| R-03 | Slow neural indexing | M | M | vector reuse, batching, incremental | measure with Ollama |
| R-04 | Wrong line mapping | L | High | single `split_lines`, invariant test, freshness check | T-01, T-22 |
| R-05 | Heuristic parser misses symbols | M | M | window fallback, tiering, Tree-sitter plan | more fixtures |
| R-06 | Model/dimension incompatibility | M | High | stored model id; explicit re-index | T-18 |
| R-07 | Index corruption | L | M | WAL, quick_check, rebuildable | T-12 |
| R-08 | Platform bugs (Win/macOS) | **M-H (untested)** | M | CI matrix | run suite on each OS |
| R-09 | Secrets inside source files get indexed and displayed | M | M | filename-based exclusion only today; add content secret scanning | ⏳ |
| R-10 | Scope creep (LLM, graph) | M | M | MVP boundaries | roadmap gates |
| R-11 | Unsupported claims about quality | M | High | evidence labels, benchmark in repo | this report |
| R-12 | Tauri shell unbuilt | H | L | engine/UI independent | build on a Rust machine |

## 50. MVP Definition and Acceptance
MVP workflow: select repo → index → (optional local embeddings) → ask → ranked evidence → open at line. **Acceptance status:**
| Criterion | Status |
|---|---|
| Repo registration, indexing, status | ✅ |
| Persistent local index with versioning | ✅ |
| Semantic + exact symbol + keyword search | ✅ (semantic = baseline; neural optional 🟡) |
| Paths + line numbers + snippet + reasons | ✅ |
| Open in editor with errors handled | ✅ (mocked launch) |
| Incremental re-index, interruption recovery | ✅ |
| Automated tests | ✅ 167 |
| Documentation + screenshots | ✅ |
| Native desktop package | ❌ scaffold only |
| Demonstrated semantic advantage over lexical | ❌ not shown |
Excluded as planned: LLM answers, cloud, multi-user, watchers, multiple vector backends.

## 51. Development Roadmap
| Phase | Deliverable | Status |
|---|---|---|
| 1 Problem research | §3–7 (named-product verification still open) | 🟡 |
| 2 Requirements | §9–14 | ✅ |
| 3 Technical experiments | scale test, benchmark | 🟡 (no neural model) |
| 4 Architecture | §21–40 | ✅ |
| 5 Indexing engine | `scanner/parsers/chunker/indexer` | ✅ |
| 6 Embedding + storage | `embeddings/store/vectors` | ✅/🟡 |
| 7 Search engine | `search.py` + benchmark | ✅ |
| 8 Desktop interface | `desktop/ui` | ✅ (browser); 📄 native shell |
| 9 Source navigation | `navigation.py` | ✅ (mocked launch) |
| 10 Incremental indexing | ✅ (watcher ⏳) |
| 11 Security & reliability | tests §42 | ✅ (scan ⏳) |
| 12 Evaluation | `docs/evaluation.md`, scale | 🟡 |
| 13 Packaging | ⏳ |
| 14 Documentation | README + docs | ✅ |
Effort estimates are not given: no schedule data exists to support them.

## 52. Version Roadmap
V1 reliable local search (this repo) → V2 better retrieval (neural model evaluation, Tree-sitter, watcher, filters) → V3 codebase understanding (imports/dependencies, graph) → V4 optional grounded AI assistance. Gate: add AI features only after the benchmark shows what retrieval alone cannot do.

## 53. Research Opportunities
| Topic | Question | Hypothesis | Variables | Data / metrics | Threats |
|---|---|---|---|---|---|
| A Semantic vs lexical | Does vector retrieval beat BM25 on code questions? | Yes for concept queries, no for identifiers | method × query class | labelled queries; Recall@K, MRR, nDCG | small/own benchmark; author bias |
| B Chunking | AST vs fixed-size | Symbol chunks improve precision | strategy | same | language coverage |
| C Hybrid | Does RRF help? | Yes if signals complementary | fusion weights | same | weights tuned on test |
| D Embedding models | Which local model is best? | Code-tuned > general | model | + latency/RAM | hardware variance |
| E Incremental | Cost of full vs incremental | order-of-magnitude saving | change size | seconds | synthetic repos |
| F Grounded explanations | Unsupported-claim rate | LLM adds unsupported claims | citation check | human labels | rater agreement |
| G Privacy trade-offs | Local vs cloud cost | | | | |
Novelty is **not** claimed; no literature or product review was done.

## 54. Success Metrics (current measured values)
| Area | Metric | Value | Caveat |
|---|---|---|---|
| Retrieval (hybrid, baseline) | Recall@5 / MRR / nDCG@5 / Hit@10 | 0.80 / 0.83 / 0.74 / 0.95 | 20 queries, 1 repo, author-written |
| Retrieval (lexical only) | same | 0.84 / 0.80 / 0.77 / 1.00 | |
| Exact-identifier queries | first result relevant (hybrid) | 3/3 (`refreshAccessToken`, `S3_BUCKET_NAME`, `/api/auth/refresh`: ranks 1, 1, 1+2) | only 3 queries |
| Performance | see §43 | | synthetic |
| Reliability | recovery/failure tests | all pass | simulated faults |
| Privacy | external requests in UI test | 0 | baseline embedder only |
| Usability | time-to-locate | **not measured** | needs user study |

## 55. Ethical and Responsible Design
Respect repository boundaries (explicit registration; symlinks never followed); never execute indexed code (only parsed as text); no telemetry; clearly list unsupported languages; transparent deletion; show uncertainty (agreement label, low-confidence banner, stale warnings); no fabricated symbols/paths (results are DB rows verified against disk); licences to be checked before distribution; if an LLM is ever added, repository text is untrusted input.

## 56. Final Architecture Review
| # | Question | Answer |
|---|---|---|
| 1 | Solves code discovery? | Retrieval + navigation work; value vs IDE tools not user-validated |
| 2 | Semantic search demonstrably useful? | **Not yet** (baseline ≈ BM25) |
| 3 | Keyword covers exact symbols? | Yes; symbol/literal retrievers ✅ |
| 4 | Vector DB necessary? | No at tested scale |
| 5 | Separate Python worker justified? | No — single Python process |
| 6 | Runs without cloud account? | Yes |
| 7 | Paths/lines reliable? | Tested invariant + freshness; heuristic parsers can err |
| 8 | Incremental without full rebuild? | Yes |
| 9 | Filesystem permissions restricted? | Yes (§40) |
| 10 | Student/small-team feasible? | Yes |
| 11 | Quality objectively evaluable? | Yes (harness), data small |
| 12 | Maintainable w/o infra? | Yes |
Key decisions (ADRs): ADR-1 hybrid staged; ADR-2 single SQLite incl. vectors; ADR-3 Python-only engine; ADR-4 loopback HTTP + token; ADR-5 baseline embedder default, Ollama optional; ADR-6 no LLM in MVP; ADR-7 vanilla UI; ADR-8 index stores source text (documented privacy trade-off).

## 57. PREFERRED SOLUTION

**Solution Name:** Semantic Local Codebase Search Engine

**Core Problem Solved:** Locating where behaviour is implemented in unfamiliar or large local repositories when the developer does not know the exact identifiers, while keeping source code on the device and linking every result to verified source lines.

**Target Users:** Primary — individual developers working on codebases they cannot send to cloud tools (Personas 1 and 7). Secondary — juniors, legacy maintainers, security engineers, OSS contributors, leads.

**Core Value:** One local query surface that combines exact-symbol, literal, keyword and meaning-based retrieval, shows *why* each result matched with a freshness check, and opens the exact file and line.

**Architecture:** Single local Python engine (scanner → symbol parser → chunker → embedder → atomic SQLite commit) with a retrieval engine (symbol, literal, BM25, vector → weighted RRF), exposed over a token-protected loopback HTTP API to a local web UI; optional thin Tauri shell (unverified scaffold).

**Technology Stack:** Python 3.10+ (stdlib `ast`, Flask, numpy, pathspec), SQLite + FTS5 (metadata, lexical index and float32 vectors in one file), vanilla HTML/CSS/JS, optional Ollama for neural embeddings, pytest + Playwright. Chosen over Rust/React/LanceDB because no measured need justifies their extra toolchains at the tested scale.

**Search Strategy:** Classify the query; run symbol/literal/BM25/vector retrievers; fuse with weighted reciprocal-rank fusion; collapse duplicates; verify freshness; report signals, an agreement-based confidence label and reasons. Reranking and LLM explanation deferred.

**Privacy Model:** No telemetry or cloud calls; embedding endpoint must be loopback unless explicitly overridden; UI makes only same-origin requests (tested); removing a repository deletes its index and never touches source; the index stores source text and should be treated as sensitive.

**Why Preferred:** Highest weighted score (C 4.10 vs B 3.85, A 3.80, D 3.10), best coverage of both identifier and concept queries, extensible; built staged so the simplest useful layer (keyword + symbols) works first. Caveat: if development feasibility is weighted at 30%, A narrowly wins (4.01 vs 3.91).

**Alternatives Rejected:** A (no conceptual matching), B (weak on exact identifiers, model-dependent), D (heavy, unsupported-claim risk, hard to evaluate). Also rejected for the MVP: mandatory Rust, separate Python worker, LanceDB/Chroma, React build chain, LLM, file watcher.

**Main Trade-offs:** Brute-force vectors (simple, exact, limited scale) vs ANN database; heuristic parsers (broad, imperfect) vs Tree-sitter; baseline embedder (offline, weak) vs neural (better, heavier); index holds source text (fast, stale display) vs privacy-minimal index.

**Main Risks:** Semantic benefit unproven (observed parity with BM25); untested on Windows/macOS and with real Ollama; secrets inside source files get indexed; native packaging unbuilt; benchmark small and author-written.

**MVP:** Register repo → index (11 languages, tiered) → ask or look up a symbol → ranked evidence with path/lines/signals/freshness → open in editor; incremental re-index; typed errors; 167 tests; docs and screenshots.

**Evaluation Plan:** Recall@K, Precision@K, MRR, nDCG@K, latency on `benchmark_acme_shop.json` and a larger independent multi-repo benchmark; rerun with neural models; synthetic scale tests plus real-repository timing; security and failure-injection tests; a time-to-locate user study versus IDE search/ripgrep.

**Future Scope:** (1) evaluate Ollama code/text embedding models; (2) Tree-sitter parsing; (3) content-based secret scanning; (4) Windows/macOS CI and Tauri packaging; (5) filesystem watcher; (6) dependency/import exploration; (7) optional grounded LLM explanations with citation verification.

**Expected Outcome:** A reproducible, privacy-preserving local code-search tool with transparent evidence, a tested engine and UI, and an honest evaluation harness — a portfolio-quality foundation whose semantic-search benefit is now a *measurable open question* rather than an assumption.
