#!/usr/bin/env python3
"""Fail the build if station code can send anything out that isn't declared.

1. Every module in src/notoryx_ops that imports a network library must be listed in
   notoryx_ops.privacy (a flow, the radio link, or a helper).
2. Every flow that carries radio data must be off in a fresh config.

Run from the repository root: python tools/privacy_check.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "notoryx_ops"

NETWORK = ("urllib", "http.client", "http.server", "socket", "socketserver", "asyncio.open_connection",
           "paho", "psycopg", "asyncpg", "aiohttp", "requests", "httpx", "websockets", "smtplib", "ftplib",
           "meshcore", "bleak", "serial")
LOCAL_NETWORK_HELPERS = ("net",)  # "from .net import ..." counts as network use


def network_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module in LOCAL_NETWORK_HELPERS:
                names = [f".{node.module}"]
            elif not node.level and node.module:
                names = [node.module]
        elif isinstance(node, ast.Attribute) and node.attr == "open_connection":
            names = ["asyncio.open_connection"]
        for n in names:
            if n.startswith(".") or any(n == m or n.startswith(m + ".") for m in NETWORK):
                found.append(n)
    return sorted(set(found))


def check() -> list[str]:
    sys.path.insert(0, str(ROOT / "src"))
    from notoryx_ops import privacy
    from notoryx_ops.config import Config

    problems: list[str] = []
    declared = privacy.declared_modules()
    for path in sorted(PKG.glob("*.py")):
        mod = path.stem
        uses = network_imports(path)
        if uses and mod not in declared:
            problems.append(f"{path.relative_to(ROOT)} can reach the network ({', '.join(uses)}) but isn't declared in"
                            " notoryx_ops/privacy.py. Add it to a flow (with what it sends and the setting that"
                            " turns it on) or to RADIO_LINK.")
    fresh = Config(path=Path("unused.toml"))
    for f in privacy.FLOWS:
        if f.radio_data and f.on(fresh):
            problems.append(f"Flow '{f.id}' sends radio data and is on by default. It must start off.")
    return problems


def main() -> int:
    problems = check()
    for p in problems:
        print(f"privacy check: {p}")
    if not problems:
        print("privacy check: OK, every network path is declared and radio data stays home by default")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
