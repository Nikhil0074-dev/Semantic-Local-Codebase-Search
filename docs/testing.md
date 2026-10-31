# Testing

Run everything: `scripts/run_tests.sh` (or `cd indexing-engine && python -m pytest`).

| Suite | Tests | What it covers |
|---|---:|---|
| `tests/unit` | 76 | tokenisation/line semantics, scanner (ignores, symlinks, limits), parsers (11 languages), chunker invariants, embeddings incl. a **mock Ollama HTTP server**, store (atomicity, schema, recovery), navigation |
| `tests/security` | 21 | traversal/absolute/UNC/NUL/symlink, root refusal, redaction, loopback enforcement |
| `tests/integration` | 52 | index lifecycle, failure injection, search behaviour, freshness, HTTP server guards |
| `tests/retrieval` | 8 | metric correctness (hand-computed), benchmark file validity, regression floor |
| `tests/ui` | 10 | Playwright/Chromium: zero external requests, XSS-as-text, keyboard, stale badge, add/remove flows, narrow layout |
| **Total** | **167** | 166 passed, 1 skipped (permission test cannot run as root) |

UI tests skip automatically if Chromium is not installed (`pip install playwright && playwright install chromium`).
The retrieval floor test is a **regression guard**, not a quality claim; floors sit below measured values.

## Defects found by testing while building (and fixed)
1. `update_settings` persisted and applied a rejected setting (remote Ollama URL) before validating it → now all-or-nothing; regression test added.
2. Repositories page rendered `[object HTMLElement]` (array passed to `replaceChildren`) — caught by a Playwright test.
3. Page overflowed horizontally on narrow windows — found from a screenshot; regression test added.
4. Ollama client retried refused connections with backoff (1.4 s search latency when Ollama was down) → now fails fast; test added.
5. JS/TS single-line methods and methods named `of`/`get` were missed by the heuristic parser; C one-line `struct` missed → fixed, tests added.
6. Status-code mapping for blocked settings (generic 400 → 403).

## Test-writing mistakes (tests were wrong, not the product)
Fixture content that tokenised to nothing; an assertion on a directory-level exclusion; over-strict "all files fail" expectation;
an over-optimistic top-5 expectation for a vocabulary-mismatch query (corrected to top-10 and documented as a weakness).

## Not tested
Windows/macOS; launching a real editor; real Ollama; screen readers / automated accessibility audit; files near the 1 MB limit
in volume; concurrent clients; dependency vulnerability scanning (`pip-audit`); disk-full behaviour; the Tauri shell.
Passing tests do not show the system is free of bugs.
