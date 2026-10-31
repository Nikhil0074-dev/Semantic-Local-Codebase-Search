"""Retrieval evaluation: Recall@K, Precision@K, MRR, nDCG@K over a manually written query -> relevant-code benchmark."""
from __future__ import annotations

import json
import math
import tempfile
import time
from pathlib import Path
from typing import Callable, Sequence

from .config import Settings
from .embeddings import HashingEmbedder
from .service import AppService


def _matches(result: dict, rel: dict) -> bool:
    if result["relative_path"] != rel["path"]:
        return False
    return rel.get("symbol") is None or result["symbol_name"] == rel["symbol"]


def score_query(results: Sequence[dict], relevant: Sequence[dict], k: int) -> dict:
    """Each relevant item can be credited once. Grades: 2 = primary answer, 1 = useful supporting code."""
    top = list(results)[:k]
    credited: set[int] = set()
    gains: list[int] = []
    first_rank = None
    for rank, res in enumerate(top, start=1):
        gain = 0
        for i, rel in enumerate(relevant):
            if i not in credited and _matches(res, rel):
                credited.add(i)
                gain = rel["grade"]
                break
        gains.append(gain)
        if gain and first_rank is None:
            first_rank = rank
    dcg = sum(g / math.log2(r + 1) for r, g in enumerate(gains, start=1))
    ideal = sorted((r["grade"] for r in relevant), reverse=True)[:k]
    idcg = sum(g / math.log2(r + 1) for r, g in enumerate(ideal, start=1))
    return {
        "recall": len(credited) / len(relevant) if relevant else 0.0,
        "precision": sum(1 for g in gains if g) / k,
        "rr": 1.0 / first_rank if first_rank else 0.0,
        "ndcg": dcg / idcg if idcg else 0.0,
        "hit": 1.0 if first_rank else 0.0,
    }


def aggregate(per_query: list[dict]) -> dict:
    n = len(per_query) or 1
    return {m: round(sum(q[m] for q in per_query) / n, 3) for m in ("recall", "precision", "rr", "ndcg", "hit")}


def evaluate_system(search: Callable[..., dict], benchmark: list[dict], mode: str, ks=(5, 10)) -> dict:
    per_k = {k: [] for k in ks}
    latencies = []
    failures = []
    for item in benchmark:
        t0 = time.perf_counter()
        res = search(item["query"], mode=mode, limit=max(ks))["results"]
        latencies.append((time.perf_counter() - t0) * 1000)
        for k in ks:
            per_k[k].append(score_query(res, item["relevant"], k))
        if score_query(res, item["relevant"], max(ks))["hit"] == 0:
            failures.append(item["query"])
    out = {"latency_ms_mean": round(sum(latencies) / len(latencies), 2), "latency_ms_max": round(max(latencies), 2),
           "misses_at_10": failures}
    for k in ks:
        a = aggregate(per_k[k])
        out[f"k{k}"] = {"recall": a["recall"], "precision": a["precision"], "mrr": a["rr"], "ndcg": a["ndcg"], "hit_rate": a["hit"]}
    return out


def run_benchmark(repo_path: str, benchmark_path: str, ks=(5, 10)) -> dict:
    """Index the repo with two embedders (with/without the concept lexicon) and compare retrieval strategies."""
    benchmark = json.loads(Path(benchmark_path).read_text(encoding="utf-8"))["queries"]
    report: dict = {"queries": len(benchmark), "systems": {}}
    for label, emb in (("hash+concepts", HashingEmbedder()), ("hash-noconcepts", HashingEmbedder(use_concepts=False))):
        with tempfile.TemporaryDirectory() as td:
            s = Settings()
            s.data_dir = td
            svc = AppService(s, embedder=emb)
            repo = svc.register_repository(repo_path)
            t0 = time.perf_counter()
            job = svc.index_blocking(repo["id"], "full")
            index_s = time.perf_counter() - t0
            report.setdefault("index", {})[label] = {"seconds": round(index_s, 3), "files": job.files_indexed, "chunks": job.chunks_written}
            modes = ["lexical", "symbol", "semantic", "auto"] if label == "hash+concepts" else ["semantic", "auto"]
            for mode in modes:
                name = {"auto": "hybrid"}.get(mode, mode)
                report["systems"][f"{name} [{label}]"] = evaluate_system(svc.search, benchmark, mode, ks)
            svc.store.close()
    return report


def to_markdown(report: dict, ks=(5, 10)) -> str:
    lines = [f"| System | Recall@{ks[0]} | Precision@{ks[0]} | MRR | nDCG@{ks[0]} | Recall@{ks[1]} | nDCG@{ks[1]} | Hit@{ks[1]} | mean ms |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, r in report["systems"].items():
        a, b = r[f"k{ks[0]}"], r[f"k{ks[1]}"]
        lines.append(f"| {name} | {a['recall']:.2f} | {a['precision']:.2f} | {a['mrr']:.2f} | {a['ndcg']:.2f} | "
                     f"{b['recall']:.2f} | {b['ndcg']:.2f} | {b['hit_rate']:.2f} | {r['latency_ms_mean']:.1f} |")
    return "\n".join(lines)
