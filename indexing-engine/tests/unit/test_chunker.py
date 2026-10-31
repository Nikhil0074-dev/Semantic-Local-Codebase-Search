from slcs.chunker import ChunkerConfig, chunk_file
from slcs.parsers import parse_units
from slcs.scanner import Settings, scan_repository
from slcs.textio import split_lines


def test_line_mapping_invariant_for_every_chunk_of_sample_repo(repo_copy):
    total = 0
    for f in scan_repository(repo_copy, Settings()).files:
        lines = split_lines(open(f.abs_path, encoding="utf-8").read())
        chunks = chunk_file(f.rel_path, f.language, lines, parse_units("\n".join(lines), f.language).units)
        for c in chunks:
            assert c.content == "\n".join(lines[c.start_line - 1:c.end_line]), (f.rel_path, c.symbol_name)
            assert 1 <= c.start_line <= c.end_line <= len(lines)
            total += 1
    assert total > 40


def test_large_symbol_is_split_with_overlap():
    lines = ["def big():"] + [f"    x{i} = {i}" for i in range(200)]
    units = parse_units("\n".join(lines), "python").units
    chunks = chunk_file("a.py", "python", lines, units, ChunkerConfig(max_lines=50, overlap=10))
    assert len(chunks) >= 4 and all(c.parts == len(chunks) for c in chunks)
    assert chunks[0].part == 1 and chunks[1].start_line <= chunks[0].end_line     # overlap
    assert all(c.symbol_name == "big" for c in chunks)


def test_module_level_code_becomes_a_chunk():
    lines = ["import os", "TIMEOUT = 5", "", "def f():", "    return 1"]
    chunks = chunk_file("a.py", "python", lines, parse_units("\n".join(lines), "python").units)
    assert [c.symbol_name for c in chunks] == ["<module>", "f"]


def test_fallback_windows_when_no_symbols():
    lines = [f"key{i}: value{i}" for i in range(130)]
    chunks = chunk_file("c.yaml", "config", lines, [], ChunkerConfig(window=60, overlap=10))
    assert len(chunks) == 3 and chunks[0].symbol_name is None and chunks[0].symbol_kind == "text"


def test_closing_brace_only_gap_is_not_a_chunk():
    lines = ["function f() {", "  return 1;", "}", "", "}"]
    chunks = chunk_file("a.js", "javascript", lines, parse_units("\n".join(lines), "javascript").units)
    assert len(chunks) == 1


def test_ids_hashes_and_embed_text():
    lines = ["def f():", "    return 1"]
    a = chunk_file("a.py", "python", lines, parse_units("\n".join(lines), "python").units)[0]
    b = chunk_file("a.py", "python", lines, parse_units("\n".join(lines), "python").units)[0]
    assert a.chunk_id == b.chunk_id and a.content_hash == b.content_hash and a.embed_hash == b.embed_hash
    assert "a.py" in a.embed_text and "f" in a.embed_text           # header gives the embedder path + symbol context
    c = chunk_file("b.py", "python", lines, parse_units("\n".join(lines), "python").units)[0]
    assert c.chunk_id != a.chunk_id and c.content_hash == a.content_hash and c.embed_hash != a.embed_hash


def test_blank_only_content_yields_nothing():
    assert chunk_file("a.py", "python", ["", "  ", ""], []) == []
