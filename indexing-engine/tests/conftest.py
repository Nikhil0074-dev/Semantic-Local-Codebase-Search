import hashlib
import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slcs.config import Settings  # noqa: E402
from slcs.embeddings import HashingEmbedder  # noqa: E402
from slcs.errors import EmbeddingError  # noqa: E402
from slcs.service import AppService  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "sample-repositories" / "acme-shop"
BENCHMARK = Path(__file__).resolve().parent / "retrieval" / "benchmark_acme_shop.json"


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.data_dir = str(tmp_path / "data")
    return s


@pytest.fixture
def repo_copy(tmp_path):
    dst = tmp_path / "acme-shop"
    shutil.copytree(SAMPLE, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


@pytest.fixture
def svc(settings):
    s = AppService(settings)
    yield s
    s.store.close()


@pytest.fixture
def indexed(svc, repo_copy):
    r = svc.register_repository(str(repo_copy))
    svc.index_blocking(r["id"])
    return svc, r["id"], repo_copy


class FlakyEmbedder(HashingEmbedder):
    """Hashing embedder that can be switched to fail, to test recovery behaviour."""
    def __init__(self):
        super().__init__()
        self.fail = False
        self.calls = 0

    def embed(self, texts, kind="document"):
        self.calls += 1
        if self.fail:
            raise EmbeddingError("simulated embedding outage")
        return super().embed(texts, kind)


@pytest.fixture
def flaky():
    return FlakyEmbedder()


class MockOllama:
    def __init__(self):
        self.state = {"mode": "ok", "dim": 8, "models": ["nomic-embed-text:latest"]}
        self.calls = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send(200, {"models": [{"name": m} for m in outer.state["models"]]})
                else:
                    self._send(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                outer.calls.append(body)
                mode = outer.state["mode"]
                if mode == "missing":
                    return self._send(404, {"error": "model not found"})
                if mode == "http500":
                    return self._send(500, {"error": "boom"})
                if mode == "garbage":
                    self.send_response(200); self.send_header("Content-Length", "3"); self.end_headers()
                    self.wfile.write(b"???"); return
                inputs = body["input"]
                embs = []
                for t in inputs:
                    seed = hashlib.sha256(t.encode()).digest()
                    embs.append([b / 255 - 0.5 for b in seed[: outer.state["dim"]]])
                if mode == "bad_count":
                    embs = embs[:-1]
                self._send(200, {"model": body["model"], "embeddings": embs})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama():
    m = MockOllama()
    yield m
    m.stop()
