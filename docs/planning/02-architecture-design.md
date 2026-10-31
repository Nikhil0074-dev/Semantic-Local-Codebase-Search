# Planning Report (Part 2 of 3) — Architecture and Design

Sections **21–40** of the required report format. Labels as in Part 1. Everything marked ✅ exists in `indexing-engine/` and is tested.

## 21. Final System Architecture
```
 Developer ──► UI (HTML/CSS/JS, served on 127.0.0.1)  ◄── optional native window: Tauri shell 📄
                    │  JSON over loopback HTTP, per-process token
                    ▼
        slcs.server (Flask)  ──►  slcs.service.AppService  (policy lives here)
                                     │
      ┌──────────────┬───────────────┼────────────────┬───────────────┐
      ▼              ▼               ▼                ▼               ▼
  scanner        indexer         search engine    navigation       settings
  (discovery,    (parse→chunk→   (symbol, literal, (chunk id →      (JSON, API
   filters)       embed→commit)   BM25, vector,     safe editor      allowlist)
      │              │            RRF, freshness)    argv)
      ▼              ▼               ▼
   parsers ─► chunker ─► Embedder (Protocol)        VectorBackend (Protocol)
                          ├─ HashingEmbedder ✅        └─ NumpyBruteForce ✅
                          └─ OllamaEmbedder 🟡             (LanceDB adapter ⏳)
                                     │
                                     ▼
                  SQLite single file: metadata + chunks + FTS5 + float32 vectors
```
Responsibilities: **UI** renders and calls the API (no business logic). **Server** authenticates and maps errors to HTTP.
**Service** enforces policy (repo registration, settings allowlist, open-by-chunk-id). **Indexer/search** are pure library code
used identically by CLI, server and tests. **Optional LLM** would be a separate, non-essential component (§32); search is
complete without it. Security boundaries: browser ↔ server (token, Host/Origin, CSP), engine ↔ filesystem (`safe_resolve`,
symlink policy), engine ↔ embedding endpoint (loopback-only guard), engine ↔ editor (argv allowlist).
Error propagation: typed `SlcsError` subclasses (`code`, `recoverable`) → HTTP status + JSON `{error:{code,message,recoverable}}` → UI banner/toast.

## 22. Module Architecture
| Module (file) | Purpose | Inputs → outputs | Failure modes | Tests |
|---|---|---|---|---|
| `config.py` | Settings, defaults, API-mutable allowlist | JSON → `Settings` | invalid values → `ValueError`; unreadable file → defaults | `test_server` settings |
| `security.py` | canonical roots, `safe_resolve`, redaction, loopback check | paths/urls/text → safe values or `PathSecurityError` | traversal, symlink escape, remote URL | `test_security` (21) |
| `scanner.py` | Discovery, ignore rules, language detection, content policy | root → files + skipped(reason) | unreadable dirs, size/limits | `test_scanner` |
| `textio.py` | One definition of "line" + hashing | bytes → text/lines | invalid UTF-8 → lossy flag | `test_text_and_io` |
| `parsers.py` | Symbol extraction (ast / heuristic / SQL) | text → `CodeUnit[]` | never raises; warning + empty list | `test_parsers` |
| `chunker.py` | Symbol chunks, gap chunks, windows; ids/hashes | lines+units → `Chunk[]` | n/a (pure) | `test_chunker` |
| `embeddings.py` | `Embedder` protocol; hashing + Ollama | texts → L2-normalised float32 | outage, 404, bad shape, dim change | `test_embeddings` |
| `store.py` | SQLite schema, atomic file replace, FTS, jobs | chunks/vectors ↔ rows | corrupt/newer schema, integrity errors | `test_store` |
| `vectors.py` | `VectorBackend` + numpy brute force | vector → (cid, cosine) | dimension mismatch rows ignored | via search tests |
| `indexer.py` | Full/incremental pipeline, recovery, reuse | repo → `JobSummary` | see §44 | `test_indexing` (16) |
| `search.py` | Query classify/validate, retrieve, fuse, dedupe, freshness | query → results | embedder down → degrade | `test_search` (19) |
| `navigation.py` | Editor argv builder, safe open | chunk → process | editor missing, unsafe path | `test_navigation` |
| `service.py` | Facade, job threads, settings, diagnostics | — | wraps all | integration |
| `server.py` | Flask API, guards, static UI | HTTP ↔ service | auth/host/origin | `test_server` (14) |
| `cli.py` | `slcs` command | argv | typed errors → exit 2 | `test_cli` |
| `evaluate.py` | metrics + benchmark runner | benchmark JSON → report | — | `test_metrics_and_benchmark` |
No circular dependencies: `config/errors/text/textio/models/security` are leaves; `store` ← `indexer/search/vectors` ← `service` ← `server/cli`.
Not built (as modules): file watcher, LLM answering service, plugin loader.

## 23. Data Flow
Index: `repo → scan → (per file) read bytes → content policy → sha256 → parse → chunk → reuse cached vectors by embed-hash / embed the rest → one SQLite transaction replaces the file's rows`.
Query: `text → validate → classify → [symbol, literal, BM25, vector] → weighted RRF → fetch rows → dedupe → freshness → results`.
Navigation: `chunk_id → DB row → safe_resolve(root, rel_path) → exists? → argv → spawn (no shell)`.

## 24. Repository Indexing
Stages as in the brief: discovery → ignore/security filters → language detection → parse → symbols → chunk → hash → embed → store → validate. ✅
* **Languages.** Tier 1 (tested, symbol-aware): Python (stdlib `ast`), JavaScript, TypeScript, Go, Java, SQL. Tier 2 (heuristic, light tests): Rust, C#, PHP, C, C++. Config (YAML/TOML/INI/JSON): windows only. Details: `docs/supported-languages.md`. No claim of complete support.
* **Exclusions (default):** `.git .hg .svn node_modules .venv venv env __pycache__ dist build out target .idea .vscode .tox caches .next .nuxt coverage .gradle .terraform bower_components`, lockfiles, `*.min.js/css`, `*.map`; sensitive names (`.env*` except `.env.example/.sample/.template`, `*.pem *.key *.p12 *.pfx *.jks id_rsa* *.kdbx credentials.json secrets.* .npmrc .pypirc .netrc`); `.gitignore` (nested, via `pathspec`); user patterns; symlinks (never followed); binary (NUL sniff); generated (marker in first 2 KB); minified (avg line > 400 chars or any line > 5000); size > 1 MB; > 50 000 files.
* Every skipped item is stored with a reason and displayed.

## 25. Code Parsing and Chunking
| Strategy | Verdict |
|---|---|
| Fixed-size text | Fallback only (`text` windows, 60 lines, 10 overlap) |
| Line windows w/ overlap | Used for oversize symbols (80 lines, 10 overlap) and unparseable files |
| AST-aware | Python ✅ |
| Symbol-based | **Default** for all parsed languages ✅ |
| Hierarchical (symbol + file) | Partial: class *header* chunk + member chunks; module-level "gap" chunk (`<module>`) for imports/constants/config |
**Invariant (tested for every chunk of the sample repo):** `chunk.content == "\n".join(file_lines[start-1:end])`. Line = split on LF, strip trailing CR, BOM removed. Comments/decorators/annotations directly above a symbol are included. Heuristic scanner masks strings/comments before brace matching; known misses: multi-line method signatures in JS/TS class bodies, macros in C/C++, exotic formatting (degrade to windows). Tree-sitter is the planned upgrade ⏳.
Embedding text = `"{language} {path}\n{kind} {symbol}\n{content}"` truncated at 3500 chars.

## 26. Embedding Model Design
Role: map chunk text and query text into one vector space; similarity ≈ conceptual relatedness.
| Option | Facts | Fit |
|---|---|---|
| `slcs-hash-v1` (built-in) ✅ | Feature hashing of identifier sub-words + bigrams + a small hand-written concept lexicon; 1024-d, deterministic, no download | **Baseline only**; not a neural model |
| Ollama `nomic-embed-text` | **[Verified]** 768-d; general text model (not code-specific); uses `search_document:`/`search_query:` prefixes (implemented as settings) | Candidate default for neural mode **[To validate on code]** |
| Ollama `mxbai-embed-large` | **[Verified]** 1024-d | Candidate |
| Ollama `all-minilm` | **[Verified]** 384-d, small | Candidate for low-RAM machines |
| Code-specific models | Not evaluated here | Evaluate in Phase 3 |
Ollama API **[Verified]**: `POST /api/embed` with `{"model","input":[…]}` → `{"embeddings":[[…]]}`. Implemented: batching (32), timeout, 2 retries with backoff (none when the connection is refused), dimension/shape validation, L2 normalisation, loopback-only guard, status via `GET /api/tags`.
**Compatibility:** the index stores `embedding_model_id` + dimension; a different model → `ModelMismatchError` (search degrades to keyword with a warning; indexing refuses) until the user confirms "re-index all", which deletes vectors only. Re-index is required on: model change, dimension change, embed-text format change (bump model id).
Licences of models/dependencies: **[To validate before any redistribution]** — not verified this session.

## 27. Vector Database Design
| | LanceDB | ChromaDB | SQLite (+FTS5) | Flat file / numpy |
|---|---|---|---|---|
| Local persistence | yes **[assumption]** | yes | yes | yes |
| Install | extra native dep | extra dep (heavier) | in Python stdlib | numpy |
| Vector query | ANN | ANN | none native | exact brute force |
| Metadata filter | yes | yes | SQL | in code |
| Transactions with metadata | separate store → drift risk | separate | **same DB** | n/a |
| Backup/recovery | directory | directory | one file | n/a |
**Decision:** one SQLite file (metadata + FTS5 + float32 BLOB vectors) + in-memory numpy matrix cache keyed by repo generation, behind `VectorBackend`. Rationale: atomic per-file replace guarantees metadata/text/vector consistency; zero extra dependencies. **[Measured]**: 24 108 chunks → median query 22.8 ms, p95 49.8 ms, index file 136 MB. Matrix memory ≈ chunks × dim × 4 B (24k × 1024 ≈ 100 MB). **Revisit trigger:** >~200k chunks per repo or p95 query > 100 ms → add a LanceDB adapter. Not built now (⏳).
Separation: repositories/files/chunks/jobs/skipped/meta in SQLite; vectors are a column of `chunks`; settings in `settings.json`; search history **not stored**.
Deletion: removing a repo deletes its rows + FTS rows in one transaction. Migration: `PRAGMA user_version` (=1); newer → refuse; corrupt → clear message to delete & re-index.

## 28. Semantic Retrieval Algorithm
```
query → validate (type, length ≤ 2000, control chars stripped, limit 1–50, mode, languages)
      → classify: literal ("…") | identifier (Foo.bar, S3_BUCKET, /api/x) | natural
      → retrievers (pool = max(50, 5·limit)):
           symbol  : name == q (tier 0) | member name == q (1) | prefix (2) | substring (3)
           literal : instr(content, q) for identifier/literal queries
           lexical : FTS5 BM25, weights symbol 5 / path 2 / body 1, stemmed sub-word tokens
           semantic: cosine over normalised vectors (hash model floor 0.08)
      → weighted RRF (k=60)    natural: all 1.0 | identifier: symbol 3, literal 2, lexical 1, semantic 0.5
      → fetch rows → collapse duplicate content hashes → one result per symbol → freshness → reasons/confidence
```
Handled: empty, over-long, identifier, natural, ambiguous (just fewer agreeing signals → low confidence), no results (message), duplicates (`duplicates` count), multiple repositories (scope select / ids). Cosine = dot product because vectors are normalised. Reranking models ⏳ (only if measured quality justifies).
**A high similarity is never treated as correctness:** each result shows which signals fired, an agreement-based confidence label (explicitly *not* a probability), and a freshness state.

## 29. Hybrid Search and Ranking
Example "Where is the refresh token logic?": `renew_credentials` (no word "refresh" in its name) is surfaced by the semantic signal; `refresh_endpoint` and `refreshAccessToken` by keyword/symbol signals; RRF merges rank positions without needing comparable score scales.
Stages: 1 symbol+semantic ✅ · 2 lexical + merge ✅ · 3 RRF ✅ (weights are hand-set; **not** tuned) · 4 reranker ⏳.
**[Measured] result (20 queries, 1 repo — see `docs/evaluation.md`):** hybrid ≈ lexical; the concept lexicon gave no measurable gain. Honest reading: no demonstrated benefit of the baseline embedder over BM25 on this data; a neural model and larger corpus are required to test the semantic hypothesis.

## 30. Direct File-Line Navigation
Chunk record: repo id, root, relative path, symbol, start/end line, content hash, language, chunk id ✅.
Editors (allowlist of argv templates, never a shell): VS Code/Cursor/VSCodium `--goto path:line:col`, Sublime `path:line:col`, JetBrains `--line N path`, Notepad++ `-nN path`; `custom` template only from `settings.json`. Windows `.cmd` launchers: paths containing `& | < > ^ % "` refused.
Before opening: chunk id → DB (client never sends a path) → `safe_resolve` (no `..`, absolute, NUL, symlink escape) → file must exist → line must lie in the indexed range. Failure modes tested: editor missing/unknown, file deleted, traversal, line out of range, spawn failure. Staleness is shown beforehand by the freshness check (compares file hash, then the chunk's own line range).
Actually launching a real editor was **not** tested (runner mocked).

## 31. Incremental Indexing and File Watching
Implemented: manual/triggered incremental runs ✅. For each file: unchanged mtime+size → skip; changed → hash; same hash → touch only; else re-parse, re-chunk, **reuse stored vectors for chunks with identical embed-text hash** (editing one function re-embeds only that function's chunks), atomic replace. Deleted/renamed/newly-excluded files → rows removed. Failed embeddings → file marked `error`, old chunks kept, retried next run. Interrupted run → committed files valid; job flagged `interrupted`; next run resumes.
Not built: OS filesystem watching with debounce ⏳ (manual refresh suffices for the MVP; spec in the brief retained for V2).

## 32. Optional Local LLM and Evidence-Based Answering
⏳ Not implemented, by decision. Design constraints for V4: LLM receives only retrieved chunks; every claim must cite `path:lines` from them; the app verifies each cited path/range exists in the retrieved set and strips/flags anything else; repository text is untrusted (prompt-injection) and cannot change tool permissions; metrics: citation correctness, unsupported-claim rate, latency, memory. Included only if measured benefit justifies cost.

## 33. Database and Data Model
```
repositories 1──* files 1──* chunks        repositories 1──* jobs     repositories 1──* skipped_files
     id PK        id PK      cid PK        meta(key,value): schema_version, embedding_model_id, embedding_dimension
     root_path U  (repo,rel_path) U         chunk_id U, rel_path, language, symbol_name/lname/kind,
     status, generation                     start_line, end_line, part, content, content_hash,
                                            embed_hash, embedding BLOB          chunks_fts(symbol,path,body) rowid = chunks.cid
```
Consistency: all rows for a file change inside one `BEGIN IMMEDIATE` transaction (chunks + FTS + vectors + file row + repo generation++). Retention: no query/search history stored. Source text *is* stored in `chunks.content` and the FTS tokens (needed to show stale results and keyword search) — **the index directory must be treated as sensitive** (trade-off documented in `docs/privacy.md`).

## 34. Search Result Data Contract
```json
{ "chunk_id":"…", "repository_id":"repo_…", "repository_name":"acme-shop", "relative_path":"backend/db/connection.py",
  "symbol_name":"init_database_pool", "symbol_kind":"function", "language":"python", "start_line":13, "end_line":24,
  "snippet":"…", "scores":{"fused":0.0331,"relative":1.0,"semantic":0.41,"semantic_rank":1,"lexical_rank":2,"bm25":4.1,
  "symbol_tier":null,"literal_match":false}, "freshness":"fresh|stale|missing", "confidence":"low|medium|high",
  "reasons":["Keyword match on: …","Semantic similarity 0.41 (semantic rank #1)."], "duplicates":0 }
```
Raw similarity (`scores.semantic`) is kept separate from the fused ranking score. Absolute paths never appear in results or diagnostics (tested); the UI addresses results by `chunk_id`.

## 35. UX/UI Design
Screens ✅ (see `docs/screenshots/`): Search (mode, scope, language chips, limit, result cards with evidence rail), Repositories (cards, progress, language bar, add/remove), Repository details (skipped-by-reason, failed files, parser notes), Settings (model, editor, limits, ignore patterns, privacy statement). Design choices: dark sidebar + light work area, dark code listings with line gutters, term highlighting, agreement/freshness badges, system fonts only (no external requests). Accessibility: skip link, landmarks, `aria-live` results, `aria-pressed` toggles, visible focus ring, labelled controls; reduced-motion respected. **Not done:** automated accessibility audit, screen-reader testing, usability study.

## 36. Technology Alternatives (evaluated)
| Layer | Candidates | Decision and reason |
|---|---|---|
| Desktop shell | Tauri · Electron · browser UI · native toolkit | **Local web UI now; Tauri as thin optional shell.** No Rust toolchain was available to build/verify it; the UI+engine are fully testable without it |
| Frontend | React+TS · Vue · vanilla | **Vanilla JS**: 3 pages, no build chain, strict CSP, nothing to patch; React+TS justified only if UI grows |
| Engine | Python · Rust · hybrid | **Python only.** Rust adds a second toolchain with no measured need at this scale |
| Parsing | `ast`, TS compiler API, Tree-sitter, regex heuristics, windows | **`ast` + heuristic scanner + windows**; Tree-sitter ⏳ |
| Embeddings | Ollama, other runtimes, in-process lib | **Hash baseline default; Ollama optional** |
| Vector store | LanceDB · Chroma · numpy | **SQLite + numpy** (§27) |
| Metadata | SQLite | **SQLite** |
| Tests | pytest · Vitest · Rust tests · Playwright | **pytest + Playwright** (no TS/Rust code to test) |

## 37. Preferred Technology Stack
| Layer | Choice | Why / downside / alternative rejected |
|---|---|---|
| Engine | Python 3.10+ (tested 3.12) | Fast to build & test; slower than Rust for huge repos; Rust rejected: no measured need |
| Web/API | Flask (loopback) | Tiny, well known; not async; FastAPI rejected: extra deps for no benefit |
| Index | SQLite + FTS5 + numpy | One file, transactional; brute force won't scale to millions; LanceDB deferred |
| Lexical | FTS5 BM25 | Built in; fixed tokenizer; custom engine rejected |
| Paths | `pathspec` for .gitignore | Correct gitignore semantics |
| UI | HTML/CSS/vanilla JS | No build; manual DOM code; React deferred |
| Embeddings | Hash baseline / Ollama | Offline default / better quality when installed |
| Shell | Tauri v2 (📄 scaffold, uncompiled) | Small native shell; unverified here |
| Tests | pytest, Playwright(Chromium) | |
Rust/Python split complexity is **not** justified for the MVP.

## 38. Project Folder Structure
```
semantic-local-codebase-search/
├── indexing-engine/   slcs/ (16 modules) · tests/{unit,integration,security,retrieval,ui} · pyproject.toml
├── desktop/           ui/ (index.html, styles.css, app.js) · src-tauri/ (📄 scaffold) · README.md
├── sample-repositories/acme-shop/   polyglot fixture incl. files that must be excluded
├── docs/              planning/ · screenshots/ · architecture, privacy, testing, evaluation, installation, supported-languages
├── scripts/           run_benchmark.py · scale_test.py · capture_screenshots.py · run_tests.sh
└── README.md · LICENSE · .gitignore
```
Only implemented directories exist; no empty placeholder modules.

## 39. API and Component Communication
Chosen: engine runs as one local process exposing **HTTP on 127.0.0.1** (`slcs serve`), used by the browser UI and the Tauri shell. Alternatives: stdio JSON-RPC (good for a sidecar; needs a client protocol), Unix socket (portability issues on Windows). HTTP chosen for testability (Flask test client, Playwright).
| Operation | Endpoint | Validation / errors |
|---|---|---|
| Register repo | `POST /api/repositories {path,name?}` | exists, is dir, not `/` or home; 409 duplicate |
| Start/cancel index | `POST /api/repositories/<id>/index {mode}` · `/cancel` | mode enum; 409 already running |
| Status | `GET /api/repositories[/<id>]` | 404 unknown |
| Search | `POST /api/search` | §28 validation → 400 |
| Open source | `POST /api/open {chunk_id,line?}` | chunk must exist; line inside range; 422 editor missing |
| Model status / switch | `GET /api/model` · `POST /api/model/reindex-all` | |
| Settings | `GET/PUT /api/settings` | allowlisted keys only (403 otherwise); rejected settings are not persisted |
| Delete repo index | `DELETE /api/repositories/<id>` | never touches source |
| Diagnostics | `GET /api/diagnostics` | no snippets/queries/abs paths |
Guards: token header on every `/api` call; `Host` and `Origin` must be loopback; JSON content-type on writes; 256 KB body cap; CSP `default-src 'self'`; `Cache-Control: no-store`. Timeouts: embedding 60 s (configurable); no server-side request timeout beyond Werkzeug defaults.

## 40. Security Architecture
| Risk | Mitigation | Verified by |
|---|---|---|
| Code sent to external services | No cloud calls; embedding URL must be loopback unless opted in; UI makes only same-origin requests | `test_remote_embedding...`, `test_page_makes_no_external_requests` |
| Path traversal / absolute paths | `safe_resolve`; chunk-id addressing | `test_security`, server open tests |
| Symlink escape | symlinks never followed or indexed; resolve check on open | scanner + security tests |
| Malicious repo content (XSS) | DOM text nodes only; CSP; no `innerHTML` with data | `test_hostile_source_code_is_rendered_as_text` |
| Resource exhaustion | file size/count limits, query length, body cap | tests |
| Local service exposure / CSRF / DNS rebinding | loopback bind, token, Host/Origin checks, JSON-only writes | `test_server` |
| Unsafe editor execution | allowlisted argv, `shell=False`, Windows `.cmd` metachar refusal, custom command file-only | `test_navigation`, settings allowlist test |
| Secrets in logs | `redact()`; logs never include snippets or queries | `test_security` |
| Prompt injection | N/A (no LLM); design rule in §32 | — |
| Dependency vulnerabilities | 3 runtime deps; run `pip-audit` in CI | **[Not run in this environment]** |
Limits of the model: the token stops *websites*, not malicious processes already running as the same user (they could read the DB directly). Secrets embedded **inside** ordinary source files are indexed and shown (risk R-09).
