"""A fake Tension Board 2 for demo mode and tests.

It builds a small SQLite database with the same tables and columns JugLogger reads
from the real one (holes placed where the holds are in the bundled TB2 layout
images), and a fake logbook that fills each session with climbs as the timer runs.
"""

from __future__ import annotations

import json
import random
import sqlite3
import uuid as uuidlib
from datetime import datetime, timedelta
from pathlib import Path


from .store import TIME_FORMAT

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


ASSETS = Path(__file__).resolve().parent / "assets"
LAYOUTS = [(10, "Tension Board 2 Mirror", 1, "tb2-mirror-12x12"), (11, "Tension Board 2 Spray", 0, "tb2-spray-12x12")]
BOARD_WIDTH_UNITS = 144.0  # 12 ft in inches


def _layout_holes(rng: random.Random, asset: str, first_id: int) -> list[tuple[int, float, float]]:
    """Fake database holes taken from the hold centers in the bundled layout image,
    converted to inches with a little jitter (a hold's bolt isn't its center)."""
    meta = json.loads((ASSETS / f"{asset}.json").read_text())
    width, height = meta["size"]
    scale = width / BOARD_WIDTH_UNITS
    return [
        (first_id + i, round((px - width / 2) / scale + rng.uniform(-0.6, 0.6), 2),
         round((height - py) / scale + 4 + rng.uniform(-0.6, 0.6), 2))
        for i, (px, py, _) in enumerate(meta["holds"])
    ]


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
    c.executemany("INSERT INTO layouts VALUES (?, 5, ?, ?)", [(i, n, m) for i, n, m, _ in LAYOUTS])
    c.executemany("INSERT INTO placement_roles VALUES (?, 5, ?, ?)", ROLES)
    c.executemany("INSERT INTO difficulty_grades VALUES (?, ?)", GRADES)

    layout_holes = {}
    all_holes = []
    for layout_id, _, mirrored, asset in LAYOUTS:
        holes = _layout_holes(rng, asset, first_id=layout_id * 1000)
        layout_holes[layout_id] = holes
        for hole_id, x, y in holes:
            mirror_id = None
            if mirrored:
                mirror_id = min(holes, key=lambda h: (h[1] + x) ** 2 + (h[2] - y) ** 2)[0]
            all_holes.append((hole_id, f"h{hole_id}", x, y, mirror_id))
            c.execute("INSERT INTO placements VALUES (?, ?, ?, 1, 6)", (hole_id, layout_id, hole_id))
    c.executemany("INSERT INTO holes VALUES (?, 5, ?, ?, ?, ?)", all_holes)
    xs = [h[2] for h in all_holes]
    ys = [h[3] for h in all_holes]
    c.execute("INSERT INTO product_sizes VALUES (7, 5, ?, ?, ?, ?, '12 x 12', 'Demo')",
              (min(xs) - 4, max(xs) + 4, min(ys) - 4, max(ys) + 4))

    for index, name in enumerate(NAMES):
        layout_id = 10 if index % 2 == 0 else 11
        climb_uuid = uuidlib.UUID(int=rng.getrandbits(128)).hex
        c.execute("INSERT INTO climbs VALUES (?, ?, ?, ?, ?, 1)",
                  (climb_uuid, layout_id, rng.choice(["tbsetter", "aaron", "crimpking"]),
                   name, _frames(rng, layout_holes[layout_id])))
        base = rng.uniform(12, 18)
        for angle in range(20, 55, 5):
            difficulty = min(20, base + (angle - 40) / 5)
            benchmark = difficulty if index % 7 == 0 else None
            c.execute("INSERT INTO climb_stats VALUES (?, ?, ?, ?, ?, ?)",
                      (climb_uuid, angle, difficulty, benchmark, rng.randint(1, 900), rng.uniform(1, 3)))
    c.commit()
    c.close()


def _frames(rng: random.Random, holes: list[tuple[int, float, float]]) -> str:
    """A plausible boulder: start low, 5-7 hands working up, a finish near the top, a few feet."""
    ys = sorted(h[2] for h in holes)
    low, high = ys[0], ys[-1]
    span = high - low
    x = rng.uniform(-40, 40)
    y = low + span * rng.uniform(0.25, 0.4)
    frames, used = [], set()

    def pick(target_x, target_y):
        hole = min((h for h in holes if h[0] not in used),
                   key=lambda h: (h[1] - target_x) ** 2 + (h[2] - target_y) ** 2)
        used.add(hole[0])
        return hole

    start = pick(x, y)
    frames.append(f"p{start[0]}r5")
    hands = rng.randint(5, 7)
    step = (high - span * 0.06 - y) / (hands + 1)
    for _ in range(hands):
        x = max(-60, min(60, x + rng.uniform(-22, 22)))
        y += step * rng.uniform(0.8, 1.2)
        frames.append(f"p{pick(x, y)[0]}r6")
    finish = pick(max(-60, min(60, x + rng.uniform(-15, 15))), high)
    frames.append(f"p{finish[0]}r7")
    for _ in range(rng.randint(2, 3)):
        frames.append(f"p{pick(start[1] + rng.uniform(-25, 25), low + span * rng.uniform(0, 0.18))[0]}r8")
    return "".join(frames)


def build(data_dir: Path) -> Path:
    database_path = data_dir / "demo-board.sqlite3"
    if not database_path.exists():
        build_board_database(database_path)
    return database_path


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
