"""Synthetic scale measurement (NOT a quality benchmark): python scripts/scale_test.py [n_files ...]
Generates Python files from templates, indexes with the offline hashing embedder, then measures incremental no-op,
single-file edit, query latency and peak RSS. Results depend heavily on hardware; always record the environment."""
import json
import platform
import random
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "indexing-engine"))
from slcs.config import Settings  # noqa: E402
from slcs.service import AppService  # noqa: E402

VERBS = ["load", "save", "validate", "refresh", "parse", "render", "sync", "retry", "encode", "audit", "charge", "notify"]
NOUNS = ["user", "order", "token", "invoice", "session", "report", "cache", "config", "payment", "inventory", "queue", "job"]


def make_repo(root: Path, n_files: int, seed=7) -> None:
    rnd = random.Random(seed)
    for i in range(n_files):
        d = root / f"pkg{i % 40}"
        d.mkdir(exist_ok=True)
        funcs = []
        for j in range(rnd.randint(4, 10)):
            v, n = rnd.choice(VERBS), rnd.choice(NOUNS)
            body = "\n".join(f"    value_{k} = {n}_{rnd.randint(0, 999)} + {k}" for k in range(rnd.randint(4, 25)))
            funcs.append(f'def {v}_{n}_{i}_{j}(arg):\n    """{v.title()} the {n} record."""\n{body}\n    return arg\n')
        (d / f"module_{i}.py").write_text("import os\n\nLIMIT = %d\n\n\n" % i + "\n\n".join(funcs))


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / 1024 / (1024 if sys.platform == "darwin" else 1), 1)


def main(sizes):
    out = {"environment": {"python": platform.python_version(), "platform": platform.platform(),
                           "cpu": platform.processor() or "n/a"}, "runs": []}
    for n in sizes:
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as data:
            make_repo(Path(src), n)
            s = Settings(); s.data_dir = data
            svc = AppService(s)
            rid = svc.register_repository(src)["id"]
            t = time.perf_counter(); job = svc.index_blocking(rid); full = time.perf_counter() - t
            t = time.perf_counter(); svc.index_blocking(rid); noop = time.perf_counter() - t
            p = Path(src) / "pkg0" / "module_0.py"
            p.write_text(p.read_text() + "\n\ndef added_function_xyz():\n    return 1\n")
            t = time.perf_counter(); j2 = svc.index_blocking(rid); edit = time.perf_counter() - t
            svc.search("where do we refresh the payment token", [rid])           # warm the vector cache
            lat = []
            for q in ["validate user order", "retry payment", "load_user_5_1", "audit inventory queue", "render report"] * 6:
                t = time.perf_counter(); svc.search(q, [rid]); lat.append((time.perf_counter() - t) * 1000)
            db = Path(data) / "index.sqlite3"
            run = {"files": n, "chunks": svc.repository_status(rid)["stats"]["chunks"], "full_index_s": round(full, 2),
                   "noop_incremental_s": round(noop, 2), "one_file_edit_s": round(edit, 3), "edit_files_reindexed": j2.files_indexed,
                   "query_ms_median": round(statistics.median(lat), 1), "query_ms_p95": round(sorted(lat)[int(len(lat) * .95) - 1], 1),
                   "db_mb": round(db.stat().st_size / 1e6, 1), "peak_rss_mb": rss_mb()}
            out["runs"].append(run)
            print(run, flush=True)
            svc.store.close()
    (ROOT / "docs" / "scale_results.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [200, 1000, 3000])
