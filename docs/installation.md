# Installation

Requirements: Python 3.10+ (tested on 3.12, Linux). Windows/macOS: expected to work, **untested**.

## Windows (PowerShell)
```powershell
cd semantic-local-codebase-search\indexing-engine
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
slcs add C:\Projects\my-app
slcs index my-app
slcs search "where do we handle expired tokens?"
slcs serve            # open http://127.0.0.1:47615/
```
## Linux / macOS
```bash
cd semantic-local-codebase-search/indexing-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
slcs add ~/projects/my-app && slcs index my-app && slcs serve
```
## Optional: neural embeddings with Ollama
1. Install Ollama (needs network once), `ollama pull nomic-embed-text`.
2. Settings → Embedding provider `ollama` (or `slcs --provider ollama …`). The endpoint must be on this machine.
3. Switching models requires **re-indexing all repositories** (the app asks first; source is never touched).

## Editor integration
Install your editor's command-line launcher (`code`, `cursor`, `subl`, `idea`, …) and choose it in Settings.

## Tests / screenshots / benchmarks
```bash
scripts/run_tests.sh                       # 167 tests
pip install playwright && playwright install chromium   # for UI tests and screenshots
python scripts/capture_screenshots.py      # docs/screenshots/
python scripts/run_benchmark.py            # docs/evaluation_results.json
python scripts/scale_test.py 200 1000 3000
```
## Native window (optional, unverified)
See `desktop/README.md` — requires Rust and the Tauri prerequisites; the scaffold has not been compiled.
