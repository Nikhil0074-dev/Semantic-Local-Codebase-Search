# Privacy

**Default: source code, embeddings, queries and results stay on your device.**

| Topic | Behaviour |
|---|---|
| Telemetry | None. No setting exists to enable it. |
| Network | Baseline embedder: none. Ollama provider: only the configured endpoint, and only if it is loopback (`127.0.0.1`, `localhost`, `::1`). Other hosts are blocked unless `allow_remote_embedding` is set (deliberately — your code would leave the machine). |
| UI | Same-origin requests only; system fonts; strict CSP. Verified by an automated browser test. |
| Local server | Binds to loopback; per-process token; Host/Origin checks. Protects against websites, **not** against other programs already running as you. |
| Stored data | `index.sqlite3` (metadata, chunk text, FTS tokens, vectors) and `settings.json` in the data dir (Linux `~/.local/share/slcs`, macOS `~/Library/Application Support/slcs`, Windows `%APPDATA%\slcs`; override with `SLCS_DATA_DIR`). |
| **Index contains source text** | Needed for keyword search and for showing results when a file changed. Treat the data dir like the repository itself; exclude it from cloud backups if needed. |
| Secrets | Files named like secrets (`.env`, `*.pem`, `*.key`, `id_rsa`, …) are never indexed. **Secrets typed inside ordinary source files are indexed** (content scanning is future work). |
| Logs | Never contain snippets or query text; secrets and your home path are redacted. |
| Deletion | Remove a repository in the UI/CLI to delete its index. Source files are never modified. Deleting the data dir erases everything. |
| Offline | After installing (and, for Ollama, pulling a model) no network is needed. Model download itself needs network. Full offline operation with Ollama was not verified in the build environment. |
