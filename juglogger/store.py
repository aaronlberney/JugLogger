"""JugLogger's own SQLite database: your board account, sessions, and a local copy
of your logbook rows from the Tension API.

All times are stored as local wall-clock time ("YYYY-MM-DD HH:MM:SS"). The Tension
API sends UTC, so `tension.fetch_logbook` converts its times before they get here.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    board TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    user_id INTEGER,
    token TEXT NOT NULL,
    connected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    uuid TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- 'ascent' (a send) or 'bid' (failed attempts)
    climb_uuid TEXT NOT NULL,
    angle INTEGER,
    is_mirror INTEGER,
    bid_count INTEGER,
    attempt_id INTEGER,
    difficulty REAL,                 -- your logged grade, for sends
    is_benchmark INTEGER,
    is_listed INTEGER,
    comment TEXT,
    climbed_at TEXT NOT NULL,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS entries_climbed_at ON entries (climbed_at);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> str:
    return datetime.now().strftime(TIME_FORMAT)


def normalize_time(value: str) -> str:
    """Accepts API times ('2026-11-08 18:02:11.123456'), HTML datetime-local values
    ('2026-11-08T18:02') and ISO strings; returns 'YYYY-MM-DD HH:MM:SS'."""
    value = value.strip().replace("T", " ").rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", TIME_FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime(TIME_FORMAT)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized time: {value!r}")


def utc_to_local(value: str, tz=None) -> str:
    """A UTC time with no zone attached (how the Tension API sends them) → this
    computer's local wall-clock time (or `tz`'s), 'YYYY-MM-DD HH:MM:SS'."""
    utc = datetime.strptime(normalize_time(value), TIME_FORMAT).replace(tzinfo=timezone.utc)
    return utc.astimezone(tz).strftime(TIME_FORMAT)


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # Account

    def save_account(self, board: str, username: str, user_id: int | None, token: str) -> None:
        with self._connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO account VALUES (?, ?, ?, ?, ?)",
                (board, username, user_id, token, now()),
            )

    def account(self, board: str) -> dict | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM account WHERE board = ?", (board,)).fetchone()
        return dict(row) if row else None

    def delete_account(self, board: str) -> None:
        with self._connect() as c:
            c.execute("DELETE FROM account WHERE board = ?", (board,))

    # Sessions

    def start_session(self, board: str, started_at: str | None = None) -> int:
        with self._connect() as c:
            cursor = c.execute(
                "INSERT INTO sessions (board, started_at, created_at) VALUES (?, ?, ?)",
                (board, normalize_time(started_at) if started_at else now(), now()),
            )
            return cursor.lastrowid

    def create_session(self, board: str, started_at: str, ended_at: str) -> int:
        session_id = self.start_session(board, started_at)
        self.update_session(session_id, ended_at=ended_at)
        return session_id

    def stop_session(self, session_id: int) -> None:
        self.update_session(session_id, ended_at=now())

    def update_session(self, session_id: int, started_at: str | None = None,
                       ended_at: str | None = None) -> None:
        with self._connect() as c:
            if started_at:
                c.execute("UPDATE sessions SET started_at = ? WHERE id = ?",
                          (normalize_time(started_at), session_id))
            if ended_at:
                c.execute("UPDATE sessions SET ended_at = ? WHERE id = ?",
                          (normalize_time(ended_at), session_id))

    def delete_session(self, session_id: int) -> None:
        with self._connect() as c:
            c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def session(self, session_id: int) -> dict | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def active_session(self) -> dict | None:
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def sessions(self) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM sessions ORDER BY started_at DESC").fetchall()
        return [dict(row) for row in rows]

    # Logbook entries

    def upsert_logbook(self, logbook: dict[str, list[dict]]) -> int:
        rows = []
        for kind, key in (("ascent", "ascents"), ("bid", "bids")):
            for raw in logbook.get(key, []):
                if not raw.get("climbed_at") or not raw.get("climb_uuid"):
                    continue
                rows.append((
                    raw["uuid"], kind, raw["climb_uuid"], raw.get("angle"),
                    int(bool(raw.get("is_mirror"))), raw.get("bid_count"),
                    raw.get("attempt_id"), raw.get("difficulty"),
                    int(bool(raw.get("is_benchmark"))),
                    int(raw.get("is_listed", True) is not False),
                    raw.get("comment"), normalize_time(raw["climbed_at"]),
                    raw.get("created_at"),
                ))
        with self._connect() as c:
            c.executemany(
                "INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            c.execute("INSERT OR REPLACE INTO meta VALUES ('last_fetch_at', ?)", (now(),))
        return len(rows)

    def entries_between(self, start: str, end: str) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM entries WHERE climbed_at >= ? AND climbed_at <= ? "
                "AND is_listed = 1 ORDER BY climbed_at",
                (start, end),
            ).fetchall()
        return [dict(row) for row in rows]

    def listed_entries(self) -> list[dict]:
        """The whole logbook, oldest first."""
        with self._connect() as c:
            rows = c.execute("SELECT * FROM entries WHERE is_listed = 1 ORDER BY climbed_at").fetchall()
        return [dict(row) for row in rows]

    def latest_entries(self, limit: int = 5) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM entries ORDER BY climbed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def meta(self, key: str) -> str | None:
        with self._connect() as c:
            row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        with self._connect() as c:
            if value is None:
                c.execute("DELETE FROM meta WHERE key = ?", (key,))
            else:
                c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
