"""Reproduce docs/evaluation.md:  python scripts/run_benchmark.py"""
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "indexing-engine"))
from slcs.evaluate import run_benchmark, to_markdown  # noqa: E402

bench = ROOT / "indexing-engine/tests/retrieval/benchmark_acme_shop.json"
rep = run_benchmark(str(ROOT / "sample-repositories/acme-shop"), str(bench))
rep["environment"] = {"python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor() or "n/a"}
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs/evaluation_results.json").write_text(json.dumps(rep, indent=2))
print(to_markdown(rep))
print(json.dumps(rep["index"], indent=2))
for name, r in rep["systems"].items():
    print(f"\nMISSES@10 {name}: {r['misses_at_10']}")
