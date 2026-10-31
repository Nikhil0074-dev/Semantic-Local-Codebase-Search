import os
from pathlib import Path

import pytest

from slcs.errors import PathSecurityError
from slcs.security import (assert_local_url, canonical_root, is_loopback_url, redact, redact_path, refuse_overbroad_root,
                           safe_resolve)


@pytest.mark.parametrize("bad", ["../x", "a/../../x", "/etc/passwd", "C:\\Windows\\x", "\\\\server\\share", "a\x00b", "",
                                 "a/..\\..\\x"])
def test_safe_resolve_rejects_hostile_paths(tmp_path, bad):
    with pytest.raises(PathSecurityError):
        safe_resolve(tmp_path, bad)


def test_safe_resolve_accepts_normal_nested_path(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.py").write_text("x")
    assert safe_resolve(tmp_path, "a/b.py") == (tmp_path / "a" / "b.py").resolve()


def test_safe_resolve_blocks_symlink_escape(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        os.symlink(outside, repo / "link.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(PathSecurityError):
        safe_resolve(repo, "link.py")


def test_canonical_root_requires_existing_directory(tmp_path):
    f = tmp_path / "f.txt"; f.write_text("x")
    with pytest.raises(PathSecurityError):
        canonical_root(tmp_path / "nope")
    with pytest.raises(PathSecurityError):
        canonical_root(f)
    assert canonical_root(tmp_path) == tmp_path.resolve()


def test_overbroad_roots_refused():
    with pytest.raises(PathSecurityError) as ei:
        refuse_overbroad_root(Path("/").resolve())
    assert ei.value.code == "path_too_broad"
    with pytest.raises(PathSecurityError):
        refuse_overbroad_root(Path.home().resolve())


def test_redaction_of_common_secrets():
    samples = {
        "password=hunter2hunter2": "hunter2",
        "api_key: 'abcd1234efgh'": "abcd1234efgh",
        "key AKIAABCDEFGHIJKLMNOP end": "AKIAABCDEFGHIJKLMNOP",
        "Authorization: Bearer abcdefghijklmnop12345": "abcdefghijklmnop12345",
        "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4": "SflKxwRJSMeKKF2QT4",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----": "MIIabc",
    }
    for text, secret in samples.items():
        out = redact(text)
        assert secret not in out and "REDACTED" in out, text
    assert redact("def refresh_access_token(): pass") == "def refresh_access_token(): pass"


def test_redact_path_hides_home():
    assert redact_path(str(Path.home() / "proj")).startswith("~")


@pytest.mark.parametrize("url,ok", [("http://127.0.0.1:11434", True), ("http://localhost:11434", True),
                                     ("http://[::1]:11434", True), ("http://10.0.0.5:11434", False),
                                     ("http://example.com", False), ("http://127.0.0.1.evil.com", False)])
def test_loopback_detection(url, ok):
    assert is_loopback_url(url) is ok


def test_assert_local_url_scheme_and_opt_in():
    with pytest.raises(PathSecurityError):
        assert_local_url("ftp://127.0.0.1", False)
    assert_local_url("http://example.com", True)
