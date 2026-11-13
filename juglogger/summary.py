"""Turn the logbook rows inside a session window into a session summary."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .boarddb import BoardDB, v_grade_rank
from .store import TIME_FORMAT

# Climbs logged a few minutes after you stop the timer still count.
END_GRACE = timedelta(minutes=5)


@dataclass
class ClimbResult:
    climb_uuid: str
    name: str
    setter: str | None
    layout: str | None
    angle: int | None
    is_mirror: bool
    grade: str | None             # the grade used for the session (see grade_source)
    displayed_grade: str | None   # the board's consensus grade at this angle
    logged_grade: str | None      # the grade you logged with your send
    is_benchmark: bool
    sent: bool
    flash: bool
    tries: int                    # every try at this climb during the session
    tries_to_send: int | None     # tries up to and including the first send
    sends: int
    first_at: str
    last_at: str


@dataclass
class SessionSummary:
    id: int | None
    started_at: str
    ended_at: str | None
    duration_seconds: int
    climbs: list[ClimbResult] = field(default_factory=list)

    @property
    def sent(self) -> list[ClimbResult]:
        return [c for c in self.climbs if c.sent]

    @property
    def send_count(self) -> int:
        return len(self.sent)

    @property
    def flash_count(self) -> int:
        return sum(1 for c in self.climbs if c.flash)

    @property
    def total_tries(self) -> int:
        return sum(c.tries for c in self.climbs)

    @property
    def hardest(self) -> ClimbResult | None:
        if not self.sent:
            return None
        return max(
            self.sent,
            key=lambda c: (v_grade_rank(c.grade), -(c.tries_to_send or 0), c.first_at),
        )

    @property
    def angles(self) -> list[int]:
        return sorted({c.angle for c in self.climbs if c.angle is not None})

    def pyramid(self) -> list[tuple[str, int]]:
        """Send counts per V grade, hardest first, with no gaps between the easiest
        and hardest grade sent."""
        return grade_pyramid(climb.grade for climb in self.sent)

    def to_dict(self) -> dict:
        hardest = self.hardest
        return {
            "id": self.id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "duration": format_duration(self.duration_seconds),
            "send_count": self.send_count,
            "flash_count": self.flash_count,
            "total_tries": self.total_tries,
            "climb_count": len(self.climbs),
            "angles": self.angles,
            "hardest": asdict(hardest) if hardest else None,
            "pyramid": self.pyramid(),
            "climbs": [asdict(c) for c in self.climbs],
        }


def grade_pyramid(grades) -> list[tuple[str, int]]:
    """Counts per V grade, hardest first, with no gaps between the easiest and the
    hardest grade. Grades that aren't V grades are left out."""
    counts: dict[int, int] = {}
    for grade in grades:
        rank = v_grade_rank(grade)
        if rank >= -1:
            counts[rank] = counts.get(rank, 0) + 1
    if not counts:
        return []
    return [
        ("VB" if rank == -1 else f"V{rank}", counts.get(rank, 0))
        for rank in range(max(counts), min(counts) - 1, -1)
    ]


def format_duration(seconds: int) -> str:
    minutes = max(0, seconds) // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _parse(value: str) -> datetime:
    return datetime.strptime(value, TIME_FORMAT)


def session_window(session: dict, now: datetime | None = None) -> tuple[str, str]:
    end = _parse(session["ended_at"]) if session.get("ended_at") else (now or datetime.now())
    return session["started_at"], (end + END_GRACE).strftime(TIME_FORMAT)


def _tries(entry: dict) -> int:
    if entry["kind"] == "ascent":
        return max(1, int(entry.get("attempt_id") or entry.get("bid_count") or 1))
    return max(1, int(entry.get("bid_count") or 1))


def summarize(session: dict, entries: list[dict], board: BoardDB,
              grade_source: str = "logged", now: datetime | None = None) -> SessionSummary:
    """grade_source: 'logged' uses the grade you logged with each send (falling back
    to the board's grade); 'displayed' always uses the board's grade."""
    groups: dict[tuple, list[dict]] = {}
    for entry in sorted(entries, key=lambda e: e["climbed_at"]):
        key = (entry["climb_uuid"], entry["angle"], bool(entry["is_mirror"]))
        groups.setdefault(key, []).append(entry)

    layout_names: dict[int, str | None] = {}
    climbs = []
    for (climb_uuid, angle, is_mirror), group in groups.items():
        climb = board.climb(climb_uuid)
        if climb and climb.layout_id not in layout_names:
            layout = board.layout(climb.layout_id) if climb.layout_id else None
            layout_names[climb.layout_id] = layout.name if layout else None
        stats = board.climb_stats(climb_uuid, angle) if angle is not None else None

        ascents = [e for e in group if e["kind"] == "ascent"]
        tries_to_send = None
        if ascents:
            first_send = ascents[0]
            before = [e for e in group if e["kind"] == "bid" and e["climbed_at"] <= first_send["climbed_at"]]
            tries_to_send = _tries(first_send) + sum(_tries(e) for e in before)

        displayed = board.v_grade(stats["display_difficulty"]) if stats else None
        logged = board.v_grade(ascents[0]["difficulty"]) if ascents else None
        grade = (logged or displayed) if grade_source == "logged" else (displayed or logged)

        climbs.append(ClimbResult(
            climb_uuid=climb_uuid,
            name=climb.name if climb else "Unknown climb",
            setter=climb.setter if climb else None,
            layout=layout_names.get(climb.layout_id) if climb else None,
            angle=angle,
            is_mirror=is_mirror,
            grade=grade,
            displayed_grade=displayed,
            logged_grade=logged,
            is_benchmark=bool((stats or {}).get("benchmark_difficulty"))
            or any(e.get("is_benchmark") for e in ascents),
            sent=bool(ascents),
            flash=tries_to_send == 1,
            tries=sum(_tries(e) for e in group),
            tries_to_send=tries_to_send,
            sends=len(ascents),
            first_at=group[0]["climbed_at"],
            last_at=group[-1]["climbed_at"],
        ))

    climbs.sort(key=lambda c: c.first_at)
    end = _parse(session["ended_at"]) if session.get("ended_at") else (now or datetime.now())
    duration = int((end - _parse(session["started_at"])).total_seconds())
    return SessionSummary(
        id=session.get("id"),
        started_at=session["started_at"],
        ended_at=session.get("ended_at"),
        duration_seconds=duration,
        climbs=climbs,
    )
