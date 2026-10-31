# Semantic Local Codebase Search Engine — Planning Report (Part 1 of 3)

**Sections 1–20: vision, problem, users, requirements, alternatives, decision, challenge.**
Parts 2 and 3 cover architecture/design (21–40) and delivery/risk/evaluation (41–57).

Evidence labels used throughout: **[Verified]** checked against a source this session · **[Measured]** produced by code in
this repository (reproducible) · **[Assumption]** reasoned but unproven · **[To validate]** needs an experiment or user study.
Implementation labels: ✅ built and tested · 🟡 built, limited testing · 📄 scaffold only · ⏳ deferred.

---

## 1. Executive Summary

Developers lose time locating functionality in unfamiliar or large repositories because exact-match tools need the right
words. This project turns a local repository into a searchable knowledge source: it indexes code into symbol-level
chunks, retrieves with **exact-symbol, literal, keyword (BM25) and vector signals fused by reciprocal-rank fusion**, and
returns real source lines with file path, line range, evidence signals, a freshness check, and one-click "open at line".

Everything runs on the user's machine. There is no telemetry and no cloud call; the only network use is an *optional*
local embedding server (Ollama), and the code refuses non-loopback endpoints unless the user explicitly opts in.

**Central question:** *Where is the functionality I need, and how do I jump straight to it?* Retrieval and navigation are
the product. A chatbot is explicitly **not** the product; LLM explanations are deferred (§32).

**Honest status of the core hypothesis [Measured]:** on the bundled 20-query benchmark the offline baseline embedder does
**not** beat plain BM25 keyword search (see `docs/evaluation.md`). The architecture is built so that a real neural
embedding model can be evaluated against the same benchmark; until then "semantic search is better" remains **[To validate]**.

## 2. Project Vision
A privacy-first, low-operational-complexity desktop tool that complements (not replaces) IDE search and ripgrep. Priorities in
order: reliable retrieval → accurate source references → privacy → low complexity → incremental updates → transparency.

## 3. Problem Statement

### 3.1–3.3 What / why / 5W1H
| Question | Investigation |
|---|---|
| What | Locating the implementation of a behaviour when you do not know the identifier names. |
| Who | Newcomers to a repo, maintainers of legacy systems, reviewers, security engineers, open-source contributors. |
| Where | Repos with many files, several languages, and logic spread over modules. |
| When | Onboarding, bug triage, feature work, review, refactoring, security audits. |
| Why | Lexical tools match tokens, not concepts; business logic is spread out; naming differs ("renew credentials" vs "refresh token"). |
| How | Combine exact + keyword + semantic retrieval, show evidence, link to source lines. |

### 3.4 Problem statement (hypothesis)
> Developers working in unfamiliar or large codebases spend considerable time locating functionality because search depends on
> exact terminology and tracing logic across files. A local retrieval system that combines symbol, keyword and semantic
> signals, and links each result to verified source lines, may shorten code discovery without sending code off-device.

**[Assumption]** The time cost is real and material; **[To validate]** with the task study in §54 (time-to-locate, baseline
= IDE search + ripgrep). No productivity numbers are claimed here; none were measured.

## 4. Root Cause Analysis (5 Whys + cause/effect)
5 Whys for "I can't find the token-refresh logic": (1) search for `refresh` returns 200 hits → (2) hits are dominated by
imports/tests/config → (3) results are ranked by text position, not concept → (4) lexical tools have no notion of
responsibility → (5) code intent is expressed in structure and vocabulary the tool doesn't model.

| Observed symptom | Immediate cause | Root cause | Impact | Investigation (status) |
|---|---|---|---|---|
| Relevant code hard to find | Exact search misses related terms | Dependence on lexical overlap | Longer investigations | Compare keyword vs semantic — **[Measured]** on sample: parity (§Eval) |
| Unrelated results | Chunks mix concepts | Poor chunk boundaries | Manual filtering | AST/symbol chunking ✅; fixed-size comparison **[To validate]** |
| Wrong line numbers | File changed after indexing | Stale metadata | Lost trust | Freshness check ✅ (tested: fresh/stale/missing) |
| Explanations without evidence | Generated without retrieval | No retrieval/generation separation | Wrong conclusions | LLM deferred; design in §32 |
| Slow indexing | Unneeded files processed | No incremental detection | Poor usability | Incremental + hash + vector reuse ✅ **[Measured]** §43 |
| Cross-language gaps | Per-language idioms | Heuristic parsers | Missed symbols | Tier table in `supported-languages.md` |

Not every cause is confirmed; the "Investigation" column states what evidence exists.

## 5. Target Users and Personas
| Persona | Goals | Pain points | Expected features | Success criteria |
|---|---|---|---|---|
| 1 Software developer | Find function, change it | Grepping many files | NL + exact search, jump to line | Finds target in ≤2 queries |
| 2 Junior developer | Learn module roles | Doesn't know names | Concept queries, context snippets | Fewer "where is X?" interruptions |
| 3 Senior/lead | Trace shared services | Cross-module logic | Multi-repo scope, filters | Reviews faster **[To validate]** |
| 4 Legacy maintainer | Explore undocumented code | Inconsistent naming | Hybrid search, robust to malformed files | Parser failures don't block indexing ✅ |
| 5 Security engineer | Find authN/authZ, secrets handling | Scattered checks | Concept search, literal search | Locates all permission checks **[To validate]** |
| 6 OSS contributor | Understand before PR | No familiarity | Fast indexing of fresh clone | Index ready in minutes |
| 7 Privacy-conscious org | No code leaves device | Cloud AI policies | Local-only, no telemetry | Verified zero external requests ✅ (UI test) |

**MVP priority: Persona 1 + 7** (individual developer who cannot send code to the cloud). Others are served incidentally.

## 6. Current Workflow Analysis
```
Question → search known keywords → open several files → read functions → follow calls/imports
        → check configuration → build mental model → locate implementation
```
Time is lost at *keyword guessing* and *file hopping*. Mistakes occur when a similarly named but irrelevant function is
accepted. The tool targets the first two steps and leaves call-graph reasoning to IDEs. It complements IDE symbol search
and ripgrep; for exact identifiers those tools are already excellent, which is why the product's exact-symbol mode is a
convenience, not the differentiator.

## 7. Existing Solutions & Competitive Analysis
Categories: text search (grep/ripgrep), IDE symbol search, language servers, code-navigation platforms, static analysis,
code-graph tools, hosted AI assistants, repository Q&A tools, local AI dev tools.

**Verification status:** capabilities of *named* commercial products were **not** verified in this session, so they are not
asserted. Cells below describe the *category* in general terms and mark unknowns. Complete during Phase 1 with current docs.

| Capability | Traditional search | IDE navigation | Hosted AI assistant | This project |
|---|---|---|---|---|
| Exact keyword | Strong | Strong | Varies **[unknown]** | ✅ built |
| Natural-language retrieval | None | Limited | Typically strong **[unknown per product]** | 🟡 baseline offline; neural via Ollama |
| Local source indexing | n/a (scan on demand) | Yes (symbols) | Varies **[unknown]** | ✅ |
| Direct line navigation | Yes (editor-dependent) | Yes | Varies | ✅ (VS Code `--goto` etc.) |
| Offline retrieval | Yes | Yes | Usually no **[unknown per product]** | ✅ with baseline embedder |
| Source-grounded explanation | No | No | Varies | ⏳ deferred |
| Incremental indexing | n/a | Yes | Varies | ✅ |

Identified gaps this project addresses: privacy by construction, evidence-first results (signals + freshness), and an
auditable, reproducible evaluation harness.

## 8. Project Objectives
**Primary:** a privacy-focused desktop application for discovering relevant code via natural-language queries with verified navigation.

| # | Objective | Completion criterion | Status |
|---|---|---|---|
| 1 | Manage local repositories | add/list/relocate/remove; removal deletes index only | ✅ tests |
| 2 | Discover files, exclude noise | dependency/build/secret/generated/binary/minified/symlink skipped with reasons | ✅ |
| 3 | Extract code units | Python via `ast`; 10 more languages via heuristics | ✅ / 🟡 |
| 4 | Local embeddings | offline baseline + Ollama provider | ✅ / 🟡 (mock-server tested) |
| 5 | Persistent local index | SQLite single file, schema-versioned | ✅ |
| 6 | Semantic + keyword retrieval | 4 retrievers + RRF | ✅ |
| 7 | Measurable ranking | Recall/Precision/MRR/nDCG harness | ✅ |
| 8 | Evidence display | path, symbol, lines, snippet, signals | ✅ |
| 9 | Open in editor | allowlisted argv, no shell | ✅ (runner mocked) |
| 10 | Incremental updates | hash + mtime + vector reuse | ✅ |
| 11 | Optional grounded explanations | — | ⏳ |
| 12 | Evaluate relevance/perf/privacy/reliability | docs/evaluation.md, scale, UI privacy test | ✅ |

## 9. Scope
| Release | Contents |
|---|---|
| **MVP (built)** | Single-user app; repo registry; discovery + exclusions; 11 languages (tiered); symbol chunking; local embeddings; SQLite index; exact + literal + BM25 + vector; RRF; paths/lines; open in editor; manual/incremental re-index; typed errors; tests; docs |
| V2 | Filesystem watching, nested-language improvements (Tree-sitter), more filters, retrieval-eval UI, search history, better recovery |
| V3 | Import/dependency exploration, code relationship graph, multi-repo workspaces, plugins |
| Future | Cross-repo analysis, retrieval-assisted debugging, semantic change analysis, research |
Explicitly **not** in the MVP: LLM answering, filesystem watcher, cloud, multi-user, microservices, multiple vector backends.

## 10. Functional Requirements
Priority: M = must, S = should, C = could. Status ✅/🟡/⏳.

| ID | Requirement | Pri | Acceptance criteria (test) | St |
|---|---|---|---|---|
| FR-01 | Register a repository folder | M | valid dir accepted; missing/file/root/home refused (`test_security`, `test_server`) | ✅ |
| FR-02 | Multiple repositories, scoped search | M | `test_multi_repository_search_and_filter` | ✅ |
| FR-03 | Show repo metadata & status | M | status API + UI | ✅ |
| FR-04 | Remove repo deletes index only | M | `test_removing_repository_deletes_index_but_never_source` | ✅ |
| FR-05 | Detect missing/relocated repo | M | `test_missing_repository_folder_preserves_index` | ✅ |
| FR-06 | Recursive discovery + language detection | M | `test_scanner` | ✅ |
| FR-07 | Ignore rules: defaults, `.gitignore` (nested), user patterns | M | scanner tests | ✅ |
| FR-08 | Skip sensitive/binary/minified/generated/oversized/symlink | M | scanner tests + `integration` skipped reasons | ✅ |
| FR-09 | Report skipped files and errors with reasons | M | repo detail page | ✅ |
| FR-10 | Functions/methods/classes/interfaces/modules/SQL as chunks | M | `test_parsers` | ✅/🟡 |
| FR-11 | Preserve exact line mapping | M | invariant test over every sample chunk | ✅ |
| FR-12 | Fallback for unparseable files | M | syntax-error + config windows tests | ✅ |
| FR-13 | NL query → ranked code | M | benchmark, `test_search` | ✅ |
| FR-14 | Validate queries (empty, long, control chars, limits) | M | `test_validation_errors` | ✅ |
| FR-15 | Language and repo filters | S | `test_language_filter` | ✅ |
| FR-16 | Explain why a result matched | M | `reasons` + evidence rail | ✅ |
| FR-17 | Handle empty / low-confidence results | M | tests + screenshot 05 | ✅ |
| FR-18 | Exact symbol / route / env var / error-code search | M | `test_search` | ✅ |
| FR-19 | Result has repo, path, symbol, lines, snippet, score, actions | M | `test_result_contract_fields` | ✅ |
| FR-20 | Open at file:line in configured editor | M | `test_navigation`, UI test | ✅ |
| FR-21 | Detect stale/missing results | M | `test_freshness_fresh_stale_missing` | ✅ |
| FR-22 | Incremental indexing, removal of stale chunks | M | `test_indexing` | ✅ |
| FR-23 | Interrupted-job recovery | M | `test_interrupted_run_is_consistent_and_resumes` | ✅ |
| FR-24 | Schema versioning; refuse newer/corrupt index | M | `test_store` | ✅ |
| FR-25 | Model compatibility; never mix vectors | M | `test_model_change_requires_explicit_reindex` | ✅ |
| FR-26 | Graceful degradation when embedder down | M | `test_semantic_degrades_gracefully...` | ✅ |
| FR-27 | Grounded LLM explanations | C | — | ⏳ |
| FR-28 | Settings (exclusions, model, editor, limits, logging) | S | settings tests/UI | ✅ |
| FR-29 | Filesystem watching | S | — | ⏳ (manual/incremental refresh instead) |

## 11. Non-Functional Requirements
| Area | Requirement | Acceptance | Status |
|---|---|---|---|
| Performance | Benchmarked on defined repos/hardware; no invented guarantees | §43 measured table | ✅ |
| Reliability | Survive embedder failure, bad files, interruption, corrupt index, missing editor | tests in §42 | ✅ |
| Usability | Status, model state, errors understandable | UI + screenshots | 🟡 (no user study) |
| Maintainability | Modular interfaces: parser, embedder, store, retrieval, navigation | `Embedder`/`VectorBackend` protocols | ✅ |
| Portability | Win/Linux/macOS | Code is path-portable; **tested only on Linux** | 🟡 |
| Privacy | Local by default | UI test: zero external requests; loopback guard | ✅ |
| Accessibility | Keyboard, contrast, focus, labels | focus/keyboard tests; no automated audit (axe) | 🟡 |
| Resource use | Light beside IDE | ~263 MB peak RSS at 24k chunks **[Measured]** | ✅ |

## 12–13. User Stories & Use Cases
15+ stories, each with acceptance criteria (condensed; IDs map to tests/screens):

| # | As a… I want… so that… | Acceptance |
|---|---|---|
| US-01 | developer, add a repository, so I can search it | valid folder registers; bad path shows error toast |
| US-02 | developer, index source files, so they're searchable | status becomes "Indexed"; counts shown |
| US-03 | developer, watch indexing progress | progress bar + counts, cancel available |
| US-04 | developer, ask a question in words, to find code without knowing names | ranked results with real lines |
| US-05 | developer, look up an exact symbol/route/env var | exact match ranked first, "exactly matches" reason |
| US-06 | developer, filter by language | only that language returned |
| US-07 | developer, open a result at its line | editor launched at `file:line:1` or actionable error |
| US-08 | developer, copy path/snippet | clipboard + toast |
| US-09 | developer, re-index after edits | only changed files re-processed |
| US-10 | developer, remove a repo | confirm dialog; index deleted; source untouched |
| US-11 | developer, keep searching when the model is down | keyword results + warning banner |
| US-12 | developer, see why files were skipped/failed | repo detail page lists reasons |
| US-13 | developer, switch embedding model safely | blocked until explicit re-index; vectors never mixed |
| US-14 | security engineer, see evidence signals | rail + reasons + agreement badge |
| US-15 | privacy-conscious dev, work offline | baseline embedder; no external requests |
| US-16 | developer, be warned about stale results | "file changed – re-index" badge |

**UC-01 Search by question** — Actor: developer. Pre: ≥1 indexed repo. Main: enter query → validate → classify → retrieve
(symbol/literal/keyword/vector) → fuse → dedupe → freshness check → render. Alt: filter language; exact mode. Errors:
empty/too long query (400, inline message); embedder down (degrade + warning); model mismatch (warning + settings link). Post:
no state changed. **UC-02 Open result** — Pre: result shown. Main: click Open → server resolves chunk id → verifies inside repo
→ builds allowlisted argv → launches. Errors: editor missing (actionable message), file deleted ("re-index"), traversal (refused).
**UC-03 Incremental index** — Main: scan → delete vanished → for each file compare mtime/size → hash → parse → chunk → reuse or
embed → atomic replace. Errors: embedding failure (file marked error, old data kept, retried next run), cancel, interrupt.

## 14–15. Solution Alternatives
| | Description | Strengths | Weaknesses |
|---|---|---|---|
| **A** Enhanced keyword | SQLite FTS5/BM25 + symbols + navigation | Simple, fast, tiny | No conceptual matching |
| **B** Local semantic | Embeddings + vector DB + UI | NL retrieval | Model dependent; weak on exact identifiers |
| **C** Hybrid + code intelligence | A + B + parsers + fusion | Covers identifier *and* concept queries | More components to evaluate |
| **D** AI-assisted platform | C + local LLM + graph + multi-step | Rich explanations | Heavy, hallucination risk, hard to evaluate |

## 16–17. Decision Criteria and Weighted Matrix
Scale 1–5 (5 best; for complexity/resource criteria 5 = least burden). Weights sum to 100.

| Criterion | Wt | A | B | C | D | Reason for scores |
|---|---:|---:|---:|---:|---:|---|
| Problem fit | 15 | 2 | 4 | 5 | 4 | A misses concept queries; B misses exact ids; C covers both |
| Search usefulness | 15 | 3 | 4 | 5 | 4 | |
| Privacy | 10 | 5 | 5 | 5 | 4 | D adds prompt-injection/leak surface |
| Development feasibility | 15 | 5 | 4 | 3 | 2 | |
| Performance | 10 | 5 | 3 | 3 | 2 | |
| Maintainability | 10 | 5 | 4 | 3 | 2 | |
| Resource efficiency | 10 | 5 | 3 | 3 | 1 | D needs an LLM resident |
| Extensibility | 10 | 2 | 4 | 5 | 5 | |
| Research potential | 5 | 2 | 3 | 5 | 4 | |
| **Weighted total** | 100 | **3.80** | **3.85** | **4.10** | **3.10** | |

**Sensitivity [Measured by script]:** equal weights → A 3.78, B 3.78, **C 4.11**, D 3.11. Privacy at 30% → C 4.30 (still first).
**Development feasibility at 30% → A 4.01 narrowly beats C 3.91.** Scores are my judgement (**[Assumption]**), not
measurements; the feasibility sensitivity is why the MVP is built as *staged* C: exact/keyword first (≈A), semantic layered on
top, LLM last.

## 18. Feasibility
Technical ✅ (built and tested). Operational ✅ (`pip install` + one command; no services). Economic ✅ (no paid APIs;
licences must still be checked per dependency — see §48). Schedule 🟡 (the MVP core was feasible for one engineer; the
Tauri shell is unbuilt). Privacy ✅ locally, verified by UI test (zero non-loopback requests) — *full offline operation with
Ollama requires first-time model download and was not testable in this sandbox*.

## 19. Preferred Solution
**Solution C, staged.** Hybrid retrieval over a local SQLite index; baseline offline embedder by default, Ollama optional; Python
engine; local UI; no LLM in the MVP. (Details in the final summary and §37.)

## 20. Challenging the Preferred Solution (skeptical review)
| Challenge | Verdict |
|---|---|
| IDEs already solve this? | For exact names yes. Concept queries and cross-language search are weaker; unproven value → user study needed. |
| Is a desktop app necessary? | No — it's a local web UI today. A shell adds native dialogs and packaging only. Tauri kept as thin, optional scaffold. |
| Is Rust required for the MVP? | **No.** Dropped from the critical path; only the optional shell is Rust. |
| Is a Python worker necessary? | Python *is* the engine (single process); no IPC layer needed in the MVP. |
| Does semantic search beat lexical? | **Not demonstrated** with the baseline embedder ([Measured] parity). Neural model comparison pending. |
| Are embeddings useful for every language? | Unknown; evaluation is on one polyglot sample only. |
| Is a vector DB needed? | No at ≤ ~25k chunks: brute-force query median 23 ms. Threshold for revisiting in §21. |
| Local inference on modest hardware? | Baseline yes; neural model cost unmeasured here. |
| Memory? | 263 MB peak at 24k chunks (synthetic). |
| Line mapping correct? | Invariant tested for all sample chunks; heuristic parsers may mis-bound unusual code. |
| Minified/generated/malformed files? | Skipped / fall back to windows ✅. |
| Secrets? | Sensitive filenames skipped; redaction in logs; but a secret *inside* a source file is indexed (see risk R-09). |
| LLM needed to prove value? | No. Removed from MVP. |
| Multi-repo worth the complexity? | Cheap here (repo_id column); kept. |
| Feasible for a student/small team? | Yes for the engine + UI; packaging is the open piece. |
Removed on this review: mandatory Rust, separate Python worker, LanceDB, React build chain, filesystem watcher, LLM.
