from slcs.text import split_words, stem, tokenize
from slcs.textio import decode_bytes, split_lines, sha256_text


def test_split_words_camel_snake_and_acronyms():
    assert split_words("refreshAccessToken") == ["refresh", "access", "token"]
    assert split_words("S3_BUCKET_NAME") == ["s3", "bucket", "name"]
    assert split_words("HTTPServerError") == ["http", "server", "error"]


def test_stem_is_consistent_between_inflections():
    assert stem("expired") == stem("expire")
    assert stem("validation") == stem("validate")
    assert stem("connection") == stem("connect") or stem("connection").startswith("connect")


def test_tokenize_drops_stopwords_and_single_chars():
    toks = tokenize("Where do we handle the JWT refresh?")
    assert "where" not in toks and "the" not in toks
    assert "jwt" in toks and "refresh" in toks


def test_split_lines_handles_crlf_and_trailing_newline():
    assert split_lines("a\r\nb\r\n") == ["a", "b"]
    assert split_lines("a\nb") == ["a", "b"]
    assert split_lines("") == [""]


def test_decode_strips_bom_and_flags_lossy():
    text, lossy = decode_bytes(b"\xef\xbb\xbfhello")
    assert text == "hello" and not lossy
    text, lossy = decode_bytes(b"ab\xff\xfecd")
    assert lossy and text.startswith("ab")


def test_hash_is_stable():
    assert sha256_text("x") == sha256_text("x") != sha256_text("y")
