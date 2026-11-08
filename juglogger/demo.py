"""A fake Tension Board 2 for demo mode and tests.

It builds a small SQLite database with the same tables and columns JugLogger reads
from the real one, a stand-in board image, and a fake logbook that fills each
session with climbs as the timer runs.
"""

from __future__ import annotations

import random
import sqlite3
import uuid as uuidlib
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .store import TIME_FORMAT

EDGES = (-64.0, 64.0, 0.0, 128.0)  # left, right, bottom, top
IMAGE_SIZE = (1080, 1080)
SPACING = 8
ROLES = [  # id, name, screen color
    (5, "start", "00DD00"),
    (6, "middle", "4444FF"),
    (7, "finish", "FF0000"),
    (8, "foot", "FF00FF"),
]
GRADES = [(10 + i, f"{font}/{v}") for i, (font, v) in enumerate([
    ("4a", "V0"), ("5a", "V1"), ("5c", "V2"), ("6a", "V3"), ("6b", "V4"), ("6c", "V5"),
    ("7a", "V6"), ("7a+", "V7"), ("7b", "V8"), ("7c", "V9"), ("7c+", "V10"),
])]
NAMES = [
    "Crimp Daddy", "Static Electricity", "Mono No", "Heel Turn", "Dyno Mite", "Pinch Me",
    "Undercling Ding", "The Slot", "Gaston Rouge", "Toe Hook Shuffle", "Lockoff Lane",
    "Sloper Season", "Big Move Energy", "Edge Lord", "Pocket Rocket", "Campus Cruiser",
    "Bumblebee", "Tension Release", "Mirror Mirror", "Spray & Pray", "Late Night Dyno",
    "Fingerboard Fiend", "Quiet Feet", "Cross Through", "Drop Knee Drama", "Kneebar Café",
    "Pump Clock", "Pebble Wrestler", "Swing Theory", "Flagging Hard", "Deadpoint Society",
    "Crux Capital", "Jug Haul", "Barn Door", "Matching Socks", "Overhang Over It",
]


def _hole_grid() -> list[tuple[int, float, float]]:
    holes, hole_id = [], 1
    left, right, bottom, top = EDGES
    y = bottom + SPACING
    while y < top:
        x = left + SPACING
        while x < right:
            holes.append((hole_id, x, y))
            hole_id += 1
            x += SPACING
        y += SPACING
    return holes


def build_board_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(42)
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE layouts (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT, is_mirrored INTEGER);
        CREATE TABLE product_sizes (id INTEGER PRIMARY KEY, product_id INTEGER, edge_left REAL,
            edge_right REAL, edge_bottom REAL, edge_top REAL, name TEXT, description TEXT);
        CREATE TABLE sets (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE product_sizes_layouts_sets (id INTEGER PRIMARY KEY, product_size_id INTEGER,
            layout_id INTEGER, set_id INTEGER, image_filename TEXT);
        CREATE TABLE holes (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT, x REAL, y REAL,
            mirrored_hole_id INTEGER);
        CREATE TABLE placements (id INTEGER PRIMARY KEY, layout_id INTEGER, hole_id INTEGER,
            set_id INTEGER, default_placement_role_id INTEGER);
        CREATE TABLE placement_roles (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT,
            screen_color TEXT);
        CREATE TABLE climbs (uuid TEXT PRIMARY KEY, layout_id INTEGER, setter_username TEXT,
            name TEXT, frames TEXT, is_listed INTEGER);
        CREATE TABLE climb_stats (climb_uuid TEXT, angle INTEGER, display_difficulty REAL,
            benchmark_difficulty REAL, ascensionist_count INTEGER, quality_average REAL);
        CREATE TABLE difficulty_grades (difficulty INTEGER PRIMARY KEY, boulder_name TEXT);
        CREATE TABLE shared_syncs (table_name TEXT PRIMARY KEY, last_synchronized_at TEXT);
    """)
    c.execute("INSERT INTO products VALUES (5, 'Tension Board 2')")
    c.executemany("INSERT INTO layouts VALUES (?, 5, ?, ?)",
                  [(10, "Tension Board 2 Mirror", 1), (11, "Tension Board 2 Spray", 0)])
    c.execute("INSERT INTO product_sizes VALUES (7, 5, ?, ?, ?, ?, '12 high x 12 wide', 'Demo')", EDGES)
    c.executemany("INSERT INTO sets VALUES (?, ?)", [(1, "Wood"), (2, "Plastic")])
    c.executemany("INSERT INTO product_sizes_layouts_sets VALUES (?, 7, ?, ?, ?)", [
        (1, 10, 1, "product_sizes_layouts_sets/demo-mirror-wood.png"),
        (2, 10, 2, "product_sizes_layouts_sets/demo-mirror-plastic.png"),
        (3, 11, 1, "product_sizes_layouts_sets/demo-spray-wood.png"),
        (4, 11, 2, "product_sizes_layouts_sets/demo-spray-plastic.png"),
    ])
    holes = _hole_grid()
    by_position = {(x, y): hole_id for hole_id, x, y in holes}
    c.executemany("INSERT INTO holes VALUES (?, 5, ?, ?, ?, ?)", [
        (hole_id, f"h{hole_id}", x, y, by_position.get((-x, y))) for hole_id, x, y in holes
    ])
    placements = []
    for layout_id in (10, 11):
        for hole_id, x, y in holes:
            set_id = 1 if (hole_id + layout_id) % 3 else 2
            placements.append((layout_id * 1000 + hole_id, layout_id, hole_id, set_id, 6))
    c.executemany("INSERT INTO placements VALUES (?, ?, ?, ?, ?)", placements)
    c.executemany("INSERT INTO placement_roles VALUES (?, 5, ?, ?)", ROLES)
    c.executemany("INSERT INTO difficulty_grades VALUES (?, ?)", GRADES)

    for index, name in enumerate(NAMES):
        layout_id = 10 if index % 2 == 0 else 11
        climb_uuid = uuidlib.UUID(int=rng.getrandbits(128)).hex
        c.execute("INSERT INTO climbs VALUES (?, ?, ?, ?, ?, 1)",
                  (climb_uuid, layout_id, rng.choice(["tbsetter", "aaron", "crimpking"]),
                   name, _frames(rng, layout_id, holes)))
        base = rng.uniform(12, 18)
        for angle in range(20, 55, 5):
            difficulty = min(20, base + (angle - 40) / 5)
            benchmark = difficulty if index % 7 == 0 else None
            c.execute("INSERT INTO climb_stats VALUES (?, ?, ?, ?, ?, ?)",
                      (climb_uuid, angle, difficulty, benchmark, rng.randint(1, 900), rng.uniform(1, 3)))
    c.commit()
    c.close()


def _frames(rng: random.Random, layout_id: int, holes) -> str:
    rows = sorted({y for _, _, y in holes})
    path_x = rng.uniform(-40, 40)
    frames = []
    for row_index, y in enumerate(rows[2:], start=2):
        if row_index % 2:
            continue
        path_x = max(-56, min(56, path_x + rng.choice([-16, -8, 0, 8, 16])))
        hole = min(holes, key=lambda h: (h[2] != y, abs(h[1] - path_x)))
        role = 5 if row_index == 2 else 7 if row_index >= len(rows) - 2 else 6
        frames.append(f"p{layout_id * 1000 + hole[0]}r{role}")
    for _ in range(rng.randint(2, 4)):
        hole = rng.choice([h for h in holes if h[2] <= 24])
        frames.append(f"p{layout_id * 1000 + hole[0]}r8")
    return "".join(frames)


def build_board_images(database_path: Path, images_dir: Path) -> None:
    """Stand-in hold images: one transparent PNG per layout and set."""
    connection = sqlite3.connect(database_path)
    rows = connection.execute(
        "SELECT psls.layout_id, psls.set_id, psls.image_filename FROM product_sizes_layouts_sets psls"
    ).fetchall()
    holes = connection.execute("SELECT id, x, y FROM holes").fetchall()
    placements = connection.execute("SELECT layout_id, hole_id, set_id FROM placements").fetchall()
    connection.close()

    left, right, bottom, top = EDGES
    width, height = IMAGE_SIZE
    hole_xy = {hole_id: (x, y) for hole_id, x, y in holes}
    colors = {1: (176, 128, 84, 255), 2: (32, 32, 34, 255)}
    for layout_id, set_id, filename in rows:
        rng = random.Random(layout_id * 10 + set_id)
        image = Image.new("RGBA", IMAGE_SIZE, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for p_layout, hole_id, p_set in placements:
            if p_layout != layout_id or p_set != set_id:
                continue
            x, y = hole_xy[hole_id]
            px = (x - left) / (right - left) * width
            py = height - (y - bottom) / (top - bottom) * height
            rx, ry = rng.uniform(14, 30), rng.uniform(10, 22)
            draw.ellipse((px - rx, py - ry, px + rx, py + ry), fill=colors[set_id])
            draw.ellipse((px - rx * .45, py - ry * .2, px + rx * .3, py + ry * .5),
                         fill=tuple(min(255, v + 40) for v in colors[set_id][:3]) + (255,))
        image = image.filter(ImageFilter.GaussianBlur(0.6))
        output = images_dir / filename
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output)


def build(data_dir: Path) -> tuple[Path, Path]:
    database_path = data_dir / "demo-board.sqlite3"
    images_dir = data_dir / "demo-images"
    if not database_path.exists() or not images_dir.exists():
        build_board_database(database_path)
        build_board_images(database_path, images_dir)
    return database_path, images_dir


class DemoLogbook:
    """Fakes the Tension API: each session gets one climb per 45 seconds of timer
    (up to 14), so the live view fills in while you watch."""

    SECONDS_PER_CLIMB = 45
    MAX_CLIMBS = 14

    def __init__(self, database_path: Path, store):
        self.database_path = database_path
        self.store = store

    def fetch_logbook(self, token: str | None = None) -> dict[str, list[dict]]:
        connection = sqlite3.connect(self.database_path)
        climbs = connection.execute("SELECT uuid FROM climbs ORDER BY name").fetchall()
        stats = {(u, a): d for u, a, d in connection.execute(
            "SELECT climb_uuid, angle, display_difficulty FROM climb_stats")}
        connection.close()

        ascents, bids = [], []
        for session in self.store.sessions():
            start = datetime.strptime(session["started_at"], TIME_FORMAT)
            end = (datetime.strptime(session["ended_at"], TIME_FORMAT)
                   if session["ended_at"] else datetime.now())
            elapsed = (end - start).total_seconds()
            count = min(self.MAX_CLIMBS, int(elapsed // self.SECONDS_PER_CLIMB))
            rng = random.Random(session["id"] * 7919)
            angle = rng.choice([30, 35, 40, 40, 45])
            picks = rng.sample(climbs, min(count, len(climbs)))
            for index, (climb_uuid,) in enumerate(picks):
                at = start + timedelta(seconds=(index + 1) * self.SECONDS_PER_CLIMB - 5)
                climbed_at = at.strftime(TIME_FORMAT)
                difficulty = stats.get((climb_uuid, angle), 15)
                sent = rng.random() < 0.72
                tries = rng.choice([1, 1, 1, 2, 2, 3, 4, 6])
                is_mirror = rng.random() < 0.15
                row_uuid = f"demo-{session['id']}-{index}"
                if sent:
                    ascents.append({
                        "uuid": row_uuid, "climb_uuid": climb_uuid, "angle": angle,
                        "is_mirror": is_mirror, "attempt_id": tries, "bid_count": tries,
                        "difficulty": round(difficulty + rng.choice([-1, 0, 0, 0, 1])),
                        "is_benchmark": False, "is_listed": True, "comment": "",
                        "climbed_at": climbed_at, "created_at": climbed_at,
                    })
                else:
                    bids.append({
                        "uuid": row_uuid, "climb_uuid": climb_uuid, "angle": angle,
                        "is_mirror": is_mirror, "bid_count": tries + 2, "comment": "",
                        "climbed_at": climbed_at, "created_at": climbed_at,
                    })
        return {"ascents": ascents, "bids": bids}
