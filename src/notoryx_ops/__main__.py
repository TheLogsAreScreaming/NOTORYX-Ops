"""Command line: run the station, write a config file, or decode a packet."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__, config, decoder


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="notoryx-ops", description="NOTORYX Ops station")
    ap.add_argument("--version", action="version", version=f"NOTORYX Ops {__version__}")
    ap.add_argument("--config", type=Path, help="config file (default: per-user location)")
    sub = ap.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="start the station and dashboard")
    run.add_argument("--demo", action="store_true", help="made-up traffic, no radio")
    run.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    run.add_argument("--host")
    run.add_argument("--port", type=int)
    sub.add_parser("init", help="write a config file with the defaults")
    sub.add_parser("where", help="show where the config and data live")
    dec = sub.add_parser("decode", help="decode one raw packet given as hex")
    dec.add_argument("hex")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    path = (args.config or config.default_config_path()).expanduser()

    if args.cmd == "init":
        if path.exists():
            print(f"Config already exists: {path}")
            return 1
        config.write_default(path)
        print(f"Wrote {path}")
        return 0
    if args.cmd == "where":
        cfg = config.load(path)
        print(f"config: {cfg.path}\ndata:   {cfg.data_dir}")
        return 0
    if args.cmd == "decode":
        try:
            print(json.dumps(decoder.decode(args.hex).to_dict(), indent=2))
        except decoder.DecodeError as exc:
            print(f"Couldn't decode: {exc}", file=sys.stderr)
            return 1
        return 0

    import uvicorn

    from .server import create_app

    cfg = config.load(path)
    if getattr(args, "demo", False):
        cfg.demo = True
        cfg.data_dir = cfg.data_dir / "demo"
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
    host = getattr(args, "host", None) or cfg.host
    port = getattr(args, "port", None) or cfg.port
    if getattr(args, "open", False):
        webbrowser.open(f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="info")
    # The station has shut down cleanly by now. Radio libraries (Bluetooth, the Meshtastic add-on) can leave
    # threads behind that Python would wait for forever, which kept the process alive after Stop or Quit and
    # held the radio. Leave right away instead.
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
