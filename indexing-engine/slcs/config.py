"""Settings: a small JSON file in the per-user data directory. No telemetry setting exists on purpose."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

EDITORS = ("vscode", "cursor", "vscodium", "sublime", "idea", "pycharm", "notepadpp", "system", "custom")


def default_data_dir() -> Path:
    env = os.environ.get("SLCS_DATA_DIR")
    if env:
        return Path(env)
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "slcs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "slcs"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "slcs"


DEFAULT_EXCLUDE_DIRS = (
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "out",
    "target", ".idea", ".vscode", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".nuxt",
    "coverage", ".gradle", ".terraform", "bower_components",
)

SENSITIVE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa", "id_rsa.*", "id_ed25519",
    "id_ecdsa", "*.kdbx", "credentials.json", "secrets.*", ".npmrc", ".pypirc", ".netrc",
)
# Template files that are meant to be committed and contain no real secrets.
SENSITIVE_ALLOW = (".env.example", ".env.sample", ".env.template")

LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "composer.lock", "poetry.lock",
             "Pipfile.lock", "go.sum", "uv.lock")


@dataclass
class Settings:
    data_dir: str = field(default_factory=lambda: str(default_data_dir()))
    embedding_provider: str = "hash"          # hash | ollama
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "nomic-embed-text"
    ollama_doc_prefix: str = "search_document: "
    ollama_query_prefix: str = "search_query: "
    allow_remote_embedding: bool = False      # privacy: embedding endpoints must be loopback unless true
    editor: str = "vscode"
    editor_command: list[str] = field(default_factory=list)   # file-only: ["myeditor", "--line", "{line}", "{path}"]
    result_limit: int = 10
    max_file_bytes: int = 1_000_000
    max_files: int = 50_000
    max_chunk_lines: int = 80
    chunk_overlap_lines: int = 10
    respect_gitignore: bool = True
    extra_ignore: list[str] = field(default_factory=list)
    log_level: str = "INFO"
    min_semantic_similarity: float = 0.08
    embed_batch_size: int = 32
    embed_timeout_s: float = 60.0

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "index.sqlite3"

    @property
    def settings_path(self) -> Path:
        return Path(self.data_dir) / "settings.json"

    def validate(self) -> None:
        if self.embedding_provider not in ("hash", "ollama"):
            raise ValueError("embedding_provider must be 'hash' or 'ollama'")
        if self.editor not in EDITORS:
            raise ValueError(f"editor must be one of {EDITORS}")
        if not 1 <= int(self.result_limit) <= 50:
            raise ValueError("result_limit must be between 1 and 50")
        if not 1_000 <= int(self.max_file_bytes) <= 20_000_000:
            raise ValueError("max_file_bytes out of range")
        if self.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError("invalid log_level")
        if not isinstance(self.extra_ignore, list) or not all(isinstance(x, str) for x in self.extra_ignore):
            raise ValueError("extra_ignore must be a list of strings")

    def to_public_dict(self) -> dict:
        return asdict(self)


# Keys that the local HTTP API is allowed to change (custom editor commands are file-only on purpose).
API_MUTABLE = {
    "embedding_provider", "ollama_url", "ollama_model", "editor", "result_limit", "max_file_bytes",
    "respect_gitignore", "extra_ignore", "log_level", "allow_remote_embedding", "max_chunk_lines",
}


def load_settings(data_dir: str | os.PathLike | None = None) -> Settings:
    s = Settings()
    if data_dir:
        s.data_dir = str(data_dir)
    p = s.settings_path
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            known = {f.name for f in fields(Settings)}
            for k, v in raw.items():
                if k in known and k != "data_dir":
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass  # unreadable settings fall back to defaults; they are never fatal
    s.validate()
    return s


def save_settings(s: Settings) -> None:
    s.validate()
    Path(s.data_dir).mkdir(parents=True, exist_ok=True)
    data = asdict(s)
    data.pop("data_dir", None)
    tmp = s.settings_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, s.settings_path)
