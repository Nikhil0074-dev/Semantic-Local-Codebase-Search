import json

from slcs.cli import main


def run(capsys, data_dir, *args):
    code = main(["--data-dir", str(data_dir), *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_add_index_search_remove(capsys, tmp_path, repo_copy):
    d = tmp_path / "cli-data"
    assert run(capsys, d, "add", str(repo_copy), "--name", "shop")[0] == 0
    code, out, _ = run(capsys, d, "repos")
    assert code == 0 and "shop" in out and "not" not in out.lower().split("shop")[0]
    code, out, _ = run(capsys, d, "index", "shop")
    assert code == 0 and json.loads(out)["status"] == "completed"
    code, out, _ = run(capsys, d, "search", "log_api_error", "--repo", "shop", "--mode", "symbol", "--limit", "2")
    assert code == 0 and "backend/logging_utils.py" in out
    code, out, _ = run(capsys, d, "search", "log_api_error", "--json", "--limit", "1")
    assert json.loads(out)["results"][0]["symbol_name"] == "log_api_error"
    assert run(capsys, d, "remove", "shop")[0] == 0
    assert run(capsys, d, "repos")[1].strip() == ""
    assert (repo_copy / "backend/logging_utils.py").exists()


def test_cli_errors_exit_2_with_code(capsys, tmp_path):
    d = tmp_path / "cli-data"
    code, _, err = run(capsys, d, "index", "nope")
    assert code == 2 and "unknown_repository" in err
    code, _, err = run(capsys, d, "add", "/definitely/missing")
    assert code == 2 and "repository_unavailable" in err
    code, _, err = run(capsys, d, "add", "/")
    assert code == 2 and "path_too_broad" in err


def test_cli_empty_query_is_a_clean_error(capsys, tmp_path, repo_copy):
    d = tmp_path / "cli-data"
    run(capsys, d, "add", str(repo_copy)); run(capsys, d, "index", "acme-shop")
    code, _, err = run(capsys, d, "search", "   ")
    assert code == 2 and "empty_query" in err
