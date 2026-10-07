"""
Start the investigator UI:   .venv/bin/python -m investigator
Import an existing run:      .venv/bin/python -m investigator --import path/to/run [more ...]
"""

import argparse
import os
import sys
import threading
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)          # tgspider.py lives next to the package


def main():
    p = argparse.ArgumentParser(prog="investigator", description="Telegram investigation workspace (local only).")
    p.add_argument("-w", "--workspace", default=os.environ.get("INVESTIGATOR_HOME", os.path.join(ROOT, "workspace")),
                   help="folder for the database, evidence store and crawl runs")
    p.add_argument("-p", "--port", type=int, default=8765)
    p.add_argument("--import", dest="imports", nargs="+", metavar="RUN_DIR",
                   help="import tgspider/tgmedia output folders, then exit")
    p.add_argument("--network-only", action="store_true",
                   help="with --import: bring in channels and links only, no posts/media")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = p.parse_args()

    if args.imports:
        from investigator import db, ingest
        con = db.connect(os.path.join(args.workspace, "investigator.db"))
        for d in args.imports:
            ingest.ingest_run(con, d, os.path.join(args.workspace, "store"), actor="cli",
                              network_only=args.network_only)
        return

    import socket
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", args.port)) == 0:
            sys.exit(f"Port {args.port} is already in use: is the investigator already running? "
                     f"Open http://127.0.0.1:{args.port}/ or use --port.")
    import uvicorn
    from investigator.server import create_app
    app = create_app(args.workspace, args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Investigator running at {url}  (workspace: {args.workspace})  Ctrl-C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
