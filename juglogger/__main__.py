"""JugLogger command line.

    python -m juglogger setup     # download the TB2 board data and sign in (run once)
    python -m juglogger doctor    # check the board data and your account
    python -m juglogger serve     # start the app at http://127.0.0.1:8000
    python -m juglogger serve --demo   # try it with a fake board and fake climbs
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from . import tension
from .boarddb import BoardDB
from .server import DEFAULT_DATA_DIR
from .store import Store

IMAGE_HOST = "https://api.tensionboardapp2.com/img"


def data_dir(args) -> Path:
    return Path(args.data or os.environ.get("JUGLOGGER_DATA") or DEFAULT_DATA_DIR)


def password() -> str:
    return os.environ.get("TENSION_PASSWORD") or getpass.getpass("Tension password: ")


def cmd_setup(args) -> int:
    import boardlib.db.aurora as aurora_db
    import requests

    root = data_dir(args)
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "tension.sqlite3"

    username = args.username or input("Tension username: ").strip()
    print("Signing in to Tension...")
    session = tension.login(username, password())
    Store(root / "juglogger.sqlite3").save_account(
        tension.BOARD, username, session["user_id"], session["token"])
    print(f"  signed in as {username} (user id {session['user_id']})")

    if not db_path.exists():
        print("Downloading the Tension board database (from the Android app, this takes a minute)...")
        aurora_db.download_database(tension.BOARD, str(db_path))
    print("Bringing the board database up to date...")
    totals = tension.sync_board_database(db_path, session["token"])
    print(f"  synced {sum(totals.values())} rows")

    board = BoardDB(db_path)
    layouts = board.layouts()
    if not layouts:
        print("!! Couldn't find the TB2 Mirror/Spray layouts in the database. Run `doctor` and share the output.")
        return 1
    images_dir = root / "tension-images"
    for layout in layouts:
        size = board.product_size_12x12(layout.product_id)
        if not size:
            print(f"!! No 12x12 size found for {layout.name}. Run `doctor` and share the output.")
            continue
        for filename in board.image_filenames(layout.id, size.id):
            target = images_dir / filename
            if target.exists():
                continue
            print(f"Downloading {layout.name} hold image {filename}")
            response = requests.get(f"{IMAGE_HOST}/{filename}", timeout=60)
            response.raise_for_status()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)
    print("\nAll set. Start the app with:  python -m juglogger serve")
    return 0


def cmd_doctor(args) -> int:
    root = data_dir(args)
    db_path = root / "tension.sqlite3"
    ok = True
    print(f"Data folder: {root}")
    if not db_path.exists():
        print("✗ Board database missing. Run: python -m juglogger setup")
        return 1
    board = BoardDB(db_path)
    print(f"✓ Board database: {db_path} ({db_path.stat().st_size // 1_000_000} MB)")
    with board._connect() as c:
        products = c.execute("SELECT id, name FROM products").fetchall()
    print("  products: " + ", ".join(f"{p['id']}={p['name']}" for p in products))
    print(f"  TB2 product id: {board.tb2_product_id}")
    layouts = board.layouts()
    if not layouts:
        ok = False
        print("✗ No TB2 Mirror/Spray layouts found")
        with board._connect() as c:
            for row in c.execute("SELECT id, product_id, name FROM layouts"):
                print(f"    layout {row['id']} (product {row['product_id']}): {row['name']}")
    for layout in layouts:
        size = board.product_size_12x12(layout.product_id)
        print(f"✓ Layout {layout.id}: {layout.name} (mirrored={layout.is_mirrored})")
        if not size:
            ok = False
            print("  ✗ No 12x12 size. Sizes for this product:")
            with board._connect() as c:
                for row in c.execute("SELECT id, name, description FROM product_sizes WHERE product_id = ?",
                                     (layout.product_id,)):
                    print(f"      {row['id']}: {row['name']} / {row['description']}")
            continue
        print(f"  size {size.id}: {size.name}  edges x {size.edge_left}..{size.edge_right}, "
              f"y {size.edge_bottom}..{size.edge_top}")
        for filename in board.image_filenames(layout.id, size.id):
            present = (root / "tension-images" / filename).exists()
            ok &= present
            print(f"  {'✓' if present else '✗'} image {filename}")
    print(f"✓ Grades: {', '.join(sorted(set(filter(None, map(board.v_grade, board.grade_names)))))}")

    store = Store(root / "juglogger.sqlite3")
    account = store.account(tension.BOARD)
    if not account:
        print("✗ Not signed in. Run setup, or sign in from the app.")
        return 1
    print(f"✓ Signed in as {account['username']}")
    try:
        logbook = tension.fetch_logbook(account["token"])
    except Exception as error:
        print(f"✗ Couldn't download your logbook: {error}")
        return 1
    print(f"✓ Logbook: {len(logbook['ascents'])} sends, {len(logbook['bids'])} attempt entries")
    rows = sorted(logbook["ascents"] + logbook["bids"], key=lambda r: r.get("climbed_at") or "")[-5:]
    print(f"  Your computer's time now: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("  Latest entries (climbed_at should be your local time when you logged them):")
    for row in rows:
        climb = board.climb(row["climb_uuid"])
        kind = "send   " if "difficulty" in row else "attempt"
        print(f"    {kind} climbed_at={row.get('climbed_at')}  created_at={row.get('created_at')}  "
              f"{row.get('angle')}°  {climb.name if climb else row['climb_uuid']}"
              f"  holds={len(board.holds(climb)) if climb else '?'}")
    print("\nEverything looks good." if ok else "\nSome checks failed; share this output.")
    return 0 if ok else 1


def cmd_serve(args) -> int:
    import uvicorn
    from .server import create_app

    app = create_app(data_dir=data_dir(args), demo_mode=args.demo)
    url = f"http://{args.host}:{args.port}"
    print(f"JugLogger{' (demo)' if args.demo else ''} running at {url}")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="juglogger")
    parser.add_argument("--data", help="data folder (default: ./data in the repo)")
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="download TB2 board data and sign in")
    setup.add_argument("--username")
    setup.set_defaults(func=cmd_setup)

    commands.add_parser("doctor", help="check board data and account").set_defaults(func=cmd_doctor)

    serve = commands.add_parser("serve", help="run the app")
    serve.add_argument("--demo", action="store_true", help="fake board and fake climbs")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-browser", action="store_true")
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    if args.command == "serve" and args.demo and not args.data:
        args.data = str(DEFAULT_DATA_DIR / "demo")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
