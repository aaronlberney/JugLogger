"""Read-only access to the Tension Board SQLite database that BoardLib downloads.

The database holds the shared, public board data: climbs, grades, holds and the
board images. Your own sends and attempts are not in it; they come from the API
(see tension.py).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

FRAME_RE = re.compile(r"p(\d+)r(\d+)")
V_GRADE_RE = re.compile(r"V(B|\d+)", re.IGNORECASE)
SIZE_12X12_RE = re.compile(r"\b12\b\D{0,12}\b12\b", re.IGNORECASE)

PRODUCT_NAME = "Tension Board 2"
LAYOUT_KEYWORDS = ("Mirror", "Spray")


@dataclass(frozen=True)
class Layout:
    id: int
    name: str
    product_id: int
    is_mirrored: bool


@dataclass(frozen=True)
class ProductSize:
    id: int
    name: str
    edge_left: float
    edge_right: float
    edge_bottom: float
    edge_top: float


@dataclass(frozen=True)
class Hold:
    x: float
    y: float
    role: str
    color: str  # "#rrggbb"


@dataclass(frozen=True)
class Climb:
    uuid: str
    name: str
    setter: str | None
    layout_id: int | None
    frames: str


def v_grade_from_name(name: str | None) -> str | None:
    """'6A/V3' -> 'V3'. Returns the whole name when there is no V grade in it."""
    if not name:
        return None
    match = V_GRADE_RE.search(name)
    return f"V{match.group(1).upper()}" if match else name


def v_grade_rank(grade: str | None) -> int:
    """Sort key for grades: VB < V0 < V1 ... Unknown grades sort lowest."""
    if not grade:
        return -2
    match = V_GRADE_RE.fullmatch(grade.strip())
    if not match:
        return -2
    value = match.group(1).upper()
    return -1 if value == "B" else int(value)


class BoardDB:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(sql, params).fetchall()

    # Grades

    @cached_property
    def grade_names(self) -> dict[int, str]:
        return {
            int(row["difficulty"]): row["boulder_name"]
            for row in self._query("SELECT difficulty, boulder_name FROM difficulty_grades")
        }

    def v_grade(self, difficulty: float | None) -> str | None:
        if difficulty is None:
            return None
        return v_grade_from_name(self.grade_names.get(int(round(float(difficulty)))))

    # Climbs

    def climb(self, uuid: str) -> Climb | None:
        rows = self._query(
            "SELECT uuid, name, setter_username, layout_id, frames FROM climbs WHERE uuid = ?",
            (uuid,),
        )
        if not rows:
            return None
        row = rows[0]
        return Climb(
            uuid=row["uuid"],
            name=row["name"] or "Untitled",
            setter=row["setter_username"],
            layout_id=row["layout_id"],
            frames=row["frames"] or "",
        )

    def climb_stats(self, uuid: str, angle: int) -> dict | None:
        rows = self._query(
            "SELECT * FROM climb_stats WHERE climb_uuid = ? AND angle = ?", (uuid, angle)
        )
        return dict(rows[0]) if rows else None

    def climbs_set_by(self, user_id: int | None, username: str | None) -> list[dict]:
        """The published climbs a user has set, newest first, each with its stats at
        every angle it has been climbed at (most ascents first). Matches on the
        setter's user id when the database has it, otherwise on the username."""
        columns = {row["name"] for row in self._query("PRAGMA table_info(climbs)")}
        if user_id and "setter_id" in columns:
            where, params = "setter_id = ?", (user_id,)
        elif username:
            where, params = "lower(setter_username) = lower(?)", (username,)
        else:
            return []
        if "is_draft" in columns:
            where += " AND is_draft = 0"
        optional = ", ".join(name if name in columns else f"NULL AS {name}" for name in ("angle", "created_at"))
        climbs = self._query(
            f"SELECT uuid, name, layout_id, {optional} FROM climbs WHERE {where} AND is_listed = 1", params)
        out = []
        for row in climbs:
            stats = self._query(
                "SELECT angle, display_difficulty, benchmark_difficulty, ascensionist_count "
                "FROM climb_stats WHERE climb_uuid = ? ORDER BY ascensionist_count DESC, angle", (row["uuid"],))
            layout = self.layout(row["layout_id"]) if row["layout_id"] else None
            out.append({
                "climb_uuid": row["uuid"],
                "name": row["name"] or "Untitled",
                "layout": layout.name if layout else None,
                "set_angle": row["angle"],
                "created_at": row["created_at"],
                "ascents": sum(s["ascensionist_count"] or 0 for s in stats),
                "angles": [{
                    "angle": s["angle"],
                    "grade": self.v_grade(s["display_difficulty"]),
                    "ascents": s["ascensionist_count"] or 0,
                    "is_classic": s["benchmark_difficulty"] is not None,
                } for s in stats],
            })
        out.sort(key=lambda climb: climb["created_at"] or "", reverse=True)
        return out

    # Layouts and sizes

    @cached_property
    def tb2_product_id(self) -> int | None:
        rows = self._query("SELECT id, name FROM products")
        for row in rows:
            if (row["name"] or "").strip().lower() == PRODUCT_NAME.lower():
                return row["id"]
        for row in rows:
            if "2" in (row["name"] or "") and "tension" in (row["name"] or "").lower():
                return row["id"]
        return None

    def layouts(self) -> list[Layout]:
        """The TB2 Mirror and Spray layouts."""
        rows = self._query(
            "SELECT id, name, product_id, is_mirrored FROM layouts WHERE product_id = ?",
            (self.tb2_product_id,),
        )
        return [
            Layout(row["id"], row["name"], row["product_id"], bool(row["is_mirrored"]))
            for row in rows
            if any(keyword.lower() in (row["name"] or "").lower() for keyword in LAYOUT_KEYWORDS)
        ]

    def layout(self, layout_id: int) -> Layout | None:
        rows = self._query(
            "SELECT id, name, product_id, is_mirrored FROM layouts WHERE id = ?", (layout_id,)
        )
        if not rows:
            return None
        row = rows[0]
        return Layout(row["id"], row["name"], row["product_id"], bool(row["is_mirrored"]))

    def product_size_12x12(self, product_id: int) -> ProductSize | None:
        rows = self._query(
            "SELECT id, name, description, edge_left, edge_right, edge_bottom, edge_top "
            "FROM product_sizes WHERE product_id = ?",
            (product_id,),
        )
        for row in rows:
            label = f"{row['name'] or ''} {row['description'] or ''}"
            if SIZE_12X12_RE.search(label):
                return ProductSize(
                    row["id"], row["name"], row["edge_left"], row["edge_right"],
                    row["edge_bottom"], row["edge_top"],
                )
        return None

    def image_filenames(self, layout_id: int, product_size_id: int) -> list[str]:
        rows = self._query(
            "SELECT image_filename FROM product_sizes_layouts_sets "
            "WHERE layout_id = ? AND product_size_id = ? AND image_filename IS NOT NULL "
            "ORDER BY set_id",
            (layout_id, product_size_id),
        )
        return [row["image_filename"] for row in rows]

    # Holds

    @cached_property
    def _roles(self) -> dict[int, tuple[str, str]]:
        return {
            row["id"]: (row["name"] or "", f"#{(row['screen_color'] or 'ffffff').lstrip('#')}")
            for row in self._query("SELECT id, name, screen_color FROM placement_roles")
        }

    def holds(self, climb: Climb, mirrored: bool = False) -> list[Hold]:
        """The lit holds of a climb, in board coordinates. A mirrored ascent uses the
        mirror image of each hole."""
        frames = [(int(p), int(r)) for p, r in FRAME_RE.findall(climb.frames)]
        if not frames:
            return []
        placement_ids = tuple(p for p, _ in frames)
        marks = ",".join("?" * len(placement_ids))
        rows = self._query(
            f"""
            SELECT placements.id AS placement_id, holes.x, holes.y,
                   mirror.x AS mirror_x, mirror.y AS mirror_y
            FROM placements
            JOIN holes ON holes.id = placements.hole_id
            LEFT JOIN holes AS mirror ON mirror.id = holes.mirrored_hole_id
            WHERE placements.id IN ({marks})
            """,
            placement_ids,
        )
        positions = {}
        for row in rows:
            if mirrored and row["mirror_x"] is not None:
                positions[row["placement_id"]] = (row["mirror_x"], row["mirror_y"])
            else:
                positions[row["placement_id"]] = (row["x"], row["y"])

        holds = []
        for placement_id, role_id in frames:
            if placement_id not in positions:
                continue
            role, color = self._roles.get(role_id, ("", "#ffffff"))
            x, y = positions[placement_id]
            holds.append(Hold(x=x, y=y, role=role, color=color))
        return holds

    def layout_hole_positions(self, layout_id: int) -> list[tuple[float, float]]:
        """Holes actually placed on a layout. Skips optional "Expansion" hold sets
        (add-on kits most boards, including this one, don't have) when the database
        has a `sets` table to tell them apart."""
        exclude_expansion = "sets" in self.tables()
        join = "JOIN sets ON sets.id = placements.set_id" if exclude_expansion else ""
        condition = "AND sets.name NOT LIKE '%Expansion%'" if exclude_expansion else ""
        rows = self._query(
            f"SELECT holes.x, holes.y FROM placements JOIN holes ON holes.id = placements.hole_id "
            f"{join} WHERE placements.layout_id = ? {condition}",
            (layout_id,),
        )
        return [(row["x"], row["y"]) for row in rows]

    # Shared-table sync bookkeeping (used to pull in climbs set after the download)

    def tables(self) -> set[str]:
        return {
            row["name"] for row in self._query("SELECT name FROM sqlite_master WHERE type='table'")
        }
