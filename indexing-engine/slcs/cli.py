"""Command line interface: `slcs add|repos|index|search|status|remove|serve|eval`."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import load_settings
from .errors import SlcsError
from .service import AppService


def _svc(args) -> AppService:
    s = load_settings(args.data_dir)
    if getattr(args, "provider", None):
        s.embedding_provider = args.provider
    return AppService(s)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="slcs", description="Semantic Local Codebase Search (local-first)")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--data-dir", help="index/settings directory (default: per-user app data)")
    p.add_argument("--provider", choices=["hash", "ollama"], help="override the embedding provider for this run")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="register a repository"); a.add_argument("path"); a.add_argument("--name")
    sub.add_parser("repos", help="list repositories")
    i = sub.add_parser("index", help="index a repository"); i.add_argument("repo"); i.add_argument("--full", action="store_true")
    s = sub.add_parser("search", help="search"); s.add_argument("query"); s.add_argument("--repo", action="append")
    s.add_argument("--lang", action="append"); s.add_argument("--limit", type=int, default=10)
    s.add_argument("--mode", default="auto", choices=["auto", "hybrid", "semantic", "lexical", "symbol"]); s.add_argument("--json", action="store_true")
    st = sub.add_parser("status", help="repository status"); st.add_argument("repo")
    r = sub.add_parser("remove", help="remove a repository and its index (never touches source)"); r.add_argument("repo")
    sv = sub.add_parser("serve", help="start the local UI/API server"); sv.add_argument("--port", type=int, default=47615)
    sv.add_argument("--host", default="127.0.0.1")
    sub.add_parser("model", help="show embedding model status")
    args = p.parse_args(argv)
    try:
        svc = _svc(args)
        if args.cmd == "add":
            r_ = svc.register_repository(args.path, args.name); print(f"registered {r_['name']} ({r_['id']})")
        elif args.cmd == "repos":
            for r_ in svc.list_repositories():
                print(f"{r_['id']}  {r_['name']:<24} {r_['status']:<18} files={r_['stats']['files']} chunks={r_['stats']['chunks']}")
        elif args.cmd == "index":
            rid = svc.resolve_repository(args.repo)
            j = svc.index_blocking(rid, "full" if args.full else "incremental")
            print(json.dumps(j.to_dict(), indent=2))
        elif args.cmd == "search":
            rids = [svc.resolve_repository(x) for x in args.repo] if args.repo else None
            res = svc.search(args.query, rids, args.lang, args.limit, args.mode)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                for w in res["warnings"]:
                    print("warning:", w)
                for k, x in enumerate(res["results"], 1):
                    print(f"{k:>2}. {x['repository_name']}:{x['relative_path']}:{x['start_line']}-{x['end_line']}  "
                          f"{x['symbol_name'] or '-'}  [{x['confidence']}, {x['freshness']}]")
                    for reason in x["reasons"][:2]:
                        print("      ", reason)
                if res["message"]:
                    print(res["message"])
        elif args.cmd == "status":
            print(json.dumps(svc.repository_status(svc.resolve_repository(args.repo)), indent=2, default=str))
        elif args.cmd == "remove":
            svc.remove_repository(svc.resolve_repository(args.repo)); print("removed index data (source untouched)")
        elif args.cmd == "model":
            print(json.dumps(svc.model_status(), indent=2))
        elif args.cmd == "serve":
            from .server import serve
            serve(svc, args.host, args.port)
        return 0
    except SlcsError as exc:
        print(f"error [{exc.code}]: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
