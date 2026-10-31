import json
import math

import pytest

from tests.conftest import BENCHMARK, SAMPLE
from slcs.evaluate import aggregate, run_benchmark, score_query


def res(*pairs):
    return [{"relative_path": p, "symbol_name": s} for p, s in pairs]


REL = [{"path": "a.py", "symbol": "f", "grade": 2}, {"path": "b.py", "symbol": "g", "grade": 1}]


def test_perfect_ranking():
    m = score_query(res(("a.py", "f"), ("b.py", "g")), REL, 5)
    assert m["recall"] == 1 and m["rr"] == 1 and m["ndcg"] == pytest.approx(1.0) and m["precision"] == pytest.approx(2 / 5)


def test_reciprocal_rank_and_recall_with_one_hit_at_rank_three():
    m = score_query(res(("x.py", "z"), ("y.py", "z"), ("a.py", "f")), REL, 5)
    assert m["rr"] == pytest.approx(1 / 3) and m["recall"] == 0.5
    dcg = 2 / math.log2(4)
    idcg = 2 / math.log2(2) + 1 / math.log2(3)
    assert m["ndcg"] == pytest.approx(dcg / idcg)


def test_miss_scores_zero_and_k_cutoff_applies():
    assert score_query(res(("x.py", "z")), REL, 5)["hit"] == 0
    assert score_query(res(("x.py", "z"), ("a.py", "f")), REL, 1)["recall"] == 0


def test_duplicate_matches_are_credited_once():
    m = score_query(res(("a.py", "f"), ("a.py", "f")), REL, 5)
    assert m["recall"] == 0.5 and m["precision"] == pytest.approx(1 / 5)


def test_symbol_none_matches_any_chunk_in_file():
    m = score_query(res(("cfg.yaml", None)), [{"path": "cfg.yaml", "symbol": None, "grade": 2}], 3)
    assert m["recall"] == 1


def test_aggregate_means():
    assert aggregate([{"recall": 1, "precision": 0, "rr": 1, "ndcg": 1, "hit": 1},
                      {"recall": 0, "precision": 0, "rr": 0, "ndcg": 0, "hit": 0}])["recall"] == 0.5


def test_benchmark_file_is_wellformed_and_ground_truth_exists_in_sample():
    data = json.loads(BENCHMARK.read_text())["queries"]
    assert len(data) >= 20
    for q in data:
        assert q["relevant"] and all(r["grade"] in (1, 2) for r in q["relevant"])
        for r in q["relevant"]:
            assert (SAMPLE / r["path"]).is_file(), r["path"]


def test_benchmark_runs_and_hybrid_clears_regression_floor():
    """Regression guard, not a quality claim: floors sit well below measured values (see docs/evaluation.md)."""
    rep = run_benchmark(str(SAMPLE), str(BENCHMARK))
    hyb = rep["systems"]["hybrid [hash+concepts]"]
    lex = rep["systems"]["lexical [hash+concepts]"]
    assert hyb["k10"]["hit_rate"] >= 0.8 and hyb["k5"]["mrr"] >= 0.5
    assert hyb["k5"]["ndcg"] >= lex["k5"]["ndcg"] - 0.05         # hybrid must not be materially worse than lexical alone
    assert hyb["latency_ms_mean"] < 500
