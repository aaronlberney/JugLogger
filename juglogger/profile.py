"""The profile: every send in the logbook, all-time, the way the Tension app's own
profile lists them, plus the totals drawn from them."""

from __future__ import annotations

from .boarddb import BoardDB, v_grade_rank
from .summary import _tries, grade_pyramid

CLASSIC_FILTERS = ("all", "classic", "other")


def build_profile(entries: list[dict], board: BoardDB, grade_source: str = "logged",
                  set_climbs: list[dict] | None = None) -> dict:
    """`entries` are logbook rows (sends and failed attempts). Every send becomes one
    ascent, newest first. A flash is a one-try send with nothing logged on that climb
    (same angle, same mirroring) before it. `set_climbs` are the climbs the profile's
    owner has set (BoardDB.climbs_set_by)."""
    climbs: dict[str, object] = {}
    stats: dict[tuple, dict | None] = {}
    layouts: dict[int, str | None] = {}
    seen: set[tuple] = set()
    ascents = []
    for entry in sorted(entries, key=lambda e: e["climbed_at"]):
        key = (entry["climb_uuid"], entry["angle"], bool(entry["is_mirror"]))
        tried_before = key in seen
        seen.add(key)
        if entry["kind"] != "ascent":
            continue
        uuid, angle = entry["climb_uuid"], entry["angle"]
        if uuid not in climbs:
            climbs[uuid] = board.climb(uuid)
        climb = climbs[uuid]
        if (uuid, angle) not in stats:
            stats[uuid, angle] = board.climb_stats(uuid, angle) if angle is not None else None
        stat = stats[uuid, angle]
        layout_id = climb.layout_id if climb else None
        if layout_id is not None and layout_id not in layouts:
            layout = board.layout(layout_id)
            layouts[layout_id] = layout.name if layout else None

        displayed = board.v_grade(stat["display_difficulty"]) if stat else None
        logged = board.v_grade(entry["difficulty"])
        tries = _tries(entry)
        ascents.append({
            "uuid": entry["uuid"],
            "climb_uuid": uuid,
            "name": climb.name if climb else "Unknown climb",
            "setter": climb.setter if climb else None,
            "layout_id": layout_id,
            "layout": layouts.get(layout_id),
            "angle": angle,
            "is_mirror": bool(entry["is_mirror"]),
            "grade": (logged or displayed) if grade_source == "logged" else (displayed or logged),
            "is_classic": bool((stat or {}).get("benchmark_difficulty")) or bool(entry.get("is_benchmark")),
            "tries": tries,
            "flash": tries == 1 and not tried_before,
            "comment": entry.get("comment") or "",
            "climbed_at": entry["climbed_at"],
        })
    ascents.reverse()

    def top(rows: list[dict]) -> str | None:
        graded = [a["grade"] for a in rows if v_grade_rank(a["grade"]) >= -1]
        return max(graded, key=v_grade_rank) if graded else None

    by_angle: dict[int, list[dict]] = {}
    for ascent in ascents:
        if ascent["angle"] is not None:
            by_angle.setdefault(ascent["angle"], []).append(ascent)

    return {
        "badges": badges(ascents, set_climbs or []),
        "set_climbs": set_climbs or [],
        "totals": {
            "ascents": len(ascents),
            "climbs": len({(a["climb_uuid"], a["angle"], a["is_mirror"]) for a in ascents}),
            "flashes": sum(a["flash"] for a in ascents),
            "top_grade": top(ascents),
            "days": len({a["climbed_at"][:10] for a in ascents}),
        },
        "pyramid": grade_pyramid(a["grade"] for a in ascents),
        "angles": [
            {"angle": angle, "ascents": len(rows), "top_grade": top(rows)}
            for angle, rows in sorted(by_angle.items())
        ],
        "ascents": ascents,
    }


def filter_ascents(ascents: list[dict], grade: str | None = None, angle: int | None = None,
                   classic: str = "all", date_from: str | None = None,
                   date_to: str | None = None) -> list[dict]:
    """The logbook filters. Dates are 'YYYY-MM-DD', both ends included. The web UI
    filters the same way in the browser; this is the reference (and is tested)."""
    out = []
    for ascent in ascents:
        day = ascent["climbed_at"][:10]
        if grade and ascent["grade"] != grade:
            continue
        if angle is not None and ascent["angle"] != angle:
            continue
        if classic == "classic" and not ascent["is_classic"]:
            continue
        if classic == "other" and ascent["is_classic"]:
            continue
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        out.append(ascent)
    return out


# Badges. A tiered badge shows the highest step reached and how far the next one is.

ASCENT_STEPS = (1, 10, 25, 50, 100, 250, 500, 1000)
FLASH_STEPS = (1, 10, 25, 50, 100, 250)
CLASSIC_STEPS = (1, 5, 10, 25, 50, 100)
DAY_STEPS = (5, 10, 25, 50, 100, 200)
BIG_DAY_STEPS = (5, 10, 15, 20)
SET_STEPS = (1, 5, 10, 25, 50)
REPEAT_STEPS = (1, 10, 50, 100, 500)   # other people's ascents of climbs you set
SIEGE_TRIES = 10
STEEP_ANGLE = 50
DOUBLE_DIGITS = 10


def _tiered(key: str, value: int, steps: tuple[int, ...], title: str, detail: str) -> dict:
    """`title` and `detail` take the step as {n}."""
    reached = [step for step in steps if value >= step]
    upcoming = [step for step in steps if value < step]
    step = reached[-1] if reached else steps[0]
    return {
        "key": key,
        "mark": str(step),
        "title": title.format(n=step),
        "detail": detail.format(n=step),
        "earned": bool(reached),
        "progress": {"value": value, "next": upcoming[0]} if upcoming else None,
    }


def _single(key: str, mark: str, title: str, detail: str, earned: bool) -> dict:
    return {"key": key, "mark": mark, "title": title, "detail": detail, "earned": earned, "progress": None}


def badges(ascents: list[dict], set_climbs: list[dict]) -> list[dict]:
    """What the logbook has earned. Every badge is always listed, earned or not."""
    graded = [a for a in ascents if v_grade_rank(a["grade"]) >= -1]
    hardest = max(graded, key=lambda a: (v_grade_rank(a["grade"]), a["climbed_at"]), default=None)
    flashes = [a for a in graded if a["flash"]]
    hardest_flash = max(flashes, key=lambda a: (v_grade_rank(a["grade"]), a["climbed_at"]), default=None)
    siege = max(ascents, key=lambda a: a["tries"], default=None)
    steepest = max((a for a in ascents if a["angle"] is not None), key=lambda a: a["angle"], default=None)
    per_day: dict[str, int] = {}
    for ascent in ascents:
        per_day[ascent["climbed_at"][:10]] = per_day.get(ascent["climbed_at"][:10], 0) + 1
    layouts = {a["layout"] for a in ascents if a["layout"]}
    repeats = sum(climb["ascents"] for climb in set_climbs)

    return [
        _tiered("ascents", len(ascents), ASCENT_STEPS, "{n} ascents", "Sends logged."),
        _single("top_grade", hardest["grade"] if hardest else "V?", "Hardest send",
                f"{hardest['name']}, {hardest['angle']}°" if hardest else "Send a graded climb.", bool(hardest)),
        _single("double_digits", f"V{DOUBLE_DIGITS}+", "Double digits", f"Send V{DOUBLE_DIGITS} or harder.",
                bool(hardest) and v_grade_rank(hardest["grade"]) >= DOUBLE_DIGITS),
        _single("hardest_flash", hardest_flash["grade"] if hardest_flash else "V?", "Hardest flash",
                f"{hardest_flash['name']}, {hardest_flash['angle']}°" if hardest_flash else "Flash a graded climb.",
                bool(hardest_flash)),
        _tiered("flashes", sum(a["flash"] for a in ascents), FLASH_STEPS, "{n} flashes", "Climbs flashed."),
        _tiered("classics", sum(a["is_classic"] for a in ascents), CLASSIC_STEPS, "{n} classics", "Classics sent."),
        _tiered("days", len(per_day), DAY_STEPS, "{n} days on", "Days with a send."),
        _tiered("big_day", max(per_day.values(), default=0), BIG_DAY_STEPS, "{n}-send day", "Most sends in one day."),
        _single("siege", f"{siege['tries']}x" if siege and siege["tries"] >= SIEGE_TRIES else f"{SIEGE_TRIES}x", "Siege",
                f"{siege['name']}, sent in {siege['tries']} tries" if siege and siege["tries"] >= SIEGE_TRIES
                else f"Send a climb that takes {SIEGE_TRIES} tries or more.",
                bool(siege) and siege["tries"] >= SIEGE_TRIES),
        _single("steep", f"{steepest['angle']}°" if steepest and steepest["angle"] >= STEEP_ANGLE else f"{STEEP_ANGLE}°",
                "Steep", f"{steepest['name']}, sent at {steepest['angle']}°" if steepest and steepest["angle"] >= STEEP_ANGLE
                else f"Send at {STEEP_ANGLE}° or steeper.", bool(steepest) and steepest["angle"] >= STEEP_ANGLE),
        _single("mirrored", "M", "Mirror image", "Send a climb mirrored.", any(a["is_mirror"] for a in ascents)),
        _single("both_boards", "2", "Both boards", "Send on Mirror and on Spray.",
                any("mirror" in name.lower() for name in layouts) and any("spray" in name.lower() for name in layouts)),
        _tiered("setter", len(set_climbs), SET_STEPS, "Setter: {n}", "Published climbs you've set."),
        _tiered("repeats", repeats, REPEAT_STEPS, "Crowd pleaser: {n}", "Ascents on climbs you've set."),
    ]
