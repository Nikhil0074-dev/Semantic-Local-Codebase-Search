# Architecture (summary)

Full design: `docs/planning/02-architecture-design.md`. In one picture:

```
UI (127.0.0.1) ─ token/Host/Origin/CSP ─► Flask API ─► AppService ─► scanner → parsers → chunker → Embedder ─► SQLite
                                                           └────────► search: symbol · literal · BM25 · vector → RRF
                                                           └────────► navigation: chunk id → safe path → editor argv
```
* One SQLite file holds metadata, FTS5 and vectors; each file's rows change in one transaction.
* `Embedder` and `VectorBackend` are protocols: Ollama / LanceDB can be swapped in without touching callers.
* Typed errors (`slcs/errors.py`) carry `code` + `recoverable` end-to-end to the UI.
