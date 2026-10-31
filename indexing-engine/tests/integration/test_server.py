import json

import pytest

from slcs.server import create_app

TOKEN = "t" * 32
H = {"X-SLCS-Token": TOKEN}


@pytest.fixture
def client(indexed, tmp_path, monkeypatch):
    svc, rid, repo = indexed
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text('<meta name="t" content="__SLCS_TOKEN__">')
    (ui / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("SLCS_UI_DIR", str(ui))
    app = create_app(svc, TOKEN)
    c = app.test_client()
    c.rid, c.repo, c.svc = rid, repo, svc
    return c


def post(c, path, body, headers=H):
    return c.post(path, data=json.dumps(body), headers={**headers, "Content-Type": "application/json"},
                  base_url="http://127.0.0.1:47615")


def get(c, path, headers=H, base="http://127.0.0.1:47615"):
    return c.get(path, headers=headers, base_url=base)


def test_api_requires_token(client):
    assert get(client, "/api/health", headers={}).status_code == 401
    assert get(client, "/api/health", headers={"X-SLCS-Token": "wrong"}).status_code == 401
    assert get(client, "/api/health").status_code == 200


def test_dns_rebinding_and_foreign_host_blocked(client):
    assert get(client, "/api/health", base="http://evil.example.com").status_code == 403
    assert get(client, "/", base="http://192.168.1.5:47615").status_code == 403
    assert get(client, "/", base="http://localhost:47615").status_code == 200


def test_foreign_origin_blocked(client):
    assert get(client, "/api/health", headers={**H, "Origin": "https://evil.example.com"}).status_code == 403
    assert get(client, "/api/health", headers={**H, "Origin": "http://127.0.0.1:47615"}).status_code == 200


def test_post_requires_json_content_type(client):
    r = client.post("/api/search", data="query=x", headers={**H, "Content-Type": "application/x-www-form-urlencoded"},
                    base_url="http://127.0.0.1:47615")
    assert r.status_code == 415


def test_index_page_embeds_token_and_sets_security_headers(client):
    r = get(client, "/", headers={})
    assert TOKEN in r.get_data(as_text=True)
    assert "default-src 'self'" in r.headers["Content-Security-Policy"] and r.headers["Cache-Control"] == "no-store"
    assert get(client, "/assets/app.js", headers={}).status_code == 200
    assert get(client, "/assets/../../etc/passwd", headers={}).status_code == 404


def test_search_endpoint_and_error_mapping(client):
    r = post(client, "/api/search", {"query": "refreshAccessToken", "repository_ids": [client.rid]})
    assert r.status_code == 200 and r.json["results"][0]["symbol_name"] == "refreshAccessToken"
    assert post(client, "/api/search", {"query": ""}).status_code == 400
    assert post(client, "/api/search", {"query": "x", "repository_ids": ["nope"]}).status_code == 404
    err = post(client, "/api/search", {"query": ""}).json["error"]
    assert err["code"] == "empty_query" and err["recoverable"] is True


def test_register_validates_paths(client, tmp_path):
    assert post(client, "/api/repositories", {"path": "/definitely/not/here"}).status_code == 400
    assert post(client, "/api/repositories", {"path": "/"}).status_code == 400
    assert post(client, "/api/repositories", {}).status_code == 400
    r = post(client, "/api/repositories", {"path": str(client.repo)})
    assert r.status_code == 409 and r.json["error"]["code"] == "already_registered"


def test_open_uses_chunk_id_and_never_accepts_paths(client, monkeypatch):
    calls = []
    monkeypatch.setattr("slcs.service.open_in_editor", lambda root, rel, line, editor, **kw: calls.append((rel, line)) or ["code", "x"])
    res = post(client, "/api/search", {"query": "log_api_error", "repository_ids": [client.rid]}).json["results"][0]
    r = post(client, "/api/open", {"chunk_id": res["chunk_id"]})
    assert r.status_code == 200 and calls == [("backend/logging_utils.py", res["start_line"])]
    assert post(client, "/api/open", {"chunk_id": "../../etc/passwd"}).status_code == 404
    assert post(client, "/api/open", {"chunk_id": res["chunk_id"], "line": 99999}).status_code in (400, 403)
    assert post(client, "/api/open", {"path": "/etc/passwd"}).status_code == 400


def test_open_reports_missing_editor_actionably(client, monkeypatch):
    monkeypatch.setattr("slcs.navigation.shutil.which", lambda n: None)
    res = post(client, "/api/search", {"query": "log_api_error", "repository_ids": [client.rid]}).json["results"][0]
    r = post(client, "/api/open", {"chunk_id": res["chunk_id"]})
    assert r.status_code == 422 and "PATH" in r.json["error"]["message"]


def test_settings_allowlist(client):
    assert post(client, "/api/repositories", {"path": str(client.repo.parent)}).status_code in (201, 400, 409)
    r = client.put("/api/settings", data=json.dumps({"data_dir": "/tmp/evil"}), headers={**H, "Content-Type": "application/json"},
                   base_url="http://127.0.0.1:47615")
    assert r.status_code == 403 and r.json["error"]["code"] == "setting_not_allowed"
    r = client.put("/api/settings", data=json.dumps({"editor_command": ["rm", "-rf"]}), headers={**H, "Content-Type": "application/json"},
                   base_url="http://127.0.0.1:47615")
    assert r.status_code == 403
    r = client.put("/api/settings", data=json.dumps({"result_limit": 5}), headers={**H, "Content-Type": "application/json"},
                   base_url="http://127.0.0.1:47615")
    assert r.status_code == 200 and r.json["result_limit"] == 5
    r = client.put("/api/settings", data=json.dumps({"editor": "rm"}), headers={**H, "Content-Type": "application/json"},
                   base_url="http://127.0.0.1:47615")
    assert r.status_code >= 400


def test_remote_embedding_cannot_be_enabled_silently(client):
    r = client.put("/api/settings", data=json.dumps({"embedding_provider": "ollama", "ollama_url": "http://example.com"}),
                   headers={**H, "Content-Type": "application/json"}, base_url="http://127.0.0.1:47615")
    assert r.status_code == 403 and r.json["error"]["code"] == "remote_embedding_blocked"


def test_repository_lifecycle_over_http(client):
    r = get(client, f"/api/repositories/{client.rid}")
    assert r.json["repository"]["status"] == "indexed"
    assert post(client, f"/api/repositories/{client.rid}/index", {"mode": "bogus"}).status_code == 400
    assert post(client, f"/api/repositories/{client.rid}/index", {"mode": "incremental"}).status_code == 202
    client.svc.wait(client.rid)
    d = client.delete(f"/api/repositories/{client.rid}", headers=H, base_url="http://127.0.0.1:47615")
    assert d.status_code == 200 and get(client, "/api/repositories").json["repositories"] == []
    assert (client.repo / "backend/logging_utils.py").exists()


def test_diagnostics_contain_no_snippets_or_queries(client):
    post(client, "/api/search", {"query": "very_secret_query_text"})
    blob = get(client, "/api/diagnostics").get_data(as_text=True)
    assert "very_secret_query_text" not in blob and "def " not in blob and "telemetry" in blob


def test_rejected_settings_are_not_persisted_or_applied(client):
    before = client.svc.settings.ollama_url
    client.put("/api/settings", data=json.dumps({"embedding_provider": "ollama", "ollama_url": "http://example.com"}),
               headers={**H, "Content-Type": "application/json"}, base_url="http://127.0.0.1:47615")
    assert client.svc.settings.embedding_provider == "hash" and client.svc.settings.ollama_url == before
    assert not client.svc.settings.settings_path.exists() or "example.com" not in client.svc.settings.settings_path.read_text()
    assert post(client, "/api/search", {"query": "refreshAccessToken"}).status_code == 200      # service still works
