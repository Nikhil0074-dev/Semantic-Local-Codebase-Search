#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../indexing-engine"
python3 -m pytest "$@"
