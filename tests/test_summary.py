from juglogger.boarddb import v_grade_from_name, v_grade_rank
from juglogger.store import Store
from juglogger.summary import session_window, summarize


def ascent(uuid, climb, at, tries=1, difficulty=16, angle=40, mirror=False):
    return {"uuid": uuid, "climb_uuid": climb, "angle": angle, "is_mirror": mirror,
            "attempt_id": tries, "bid_count": tries, "difficulty": difficulty,
            "is_benchmark": False, "is_listed": True, "climbed_at": at}


def bid(uuid, climb, at, count=1, angle=40):
    return {"uuid": uuid, "climb_uuid": climb, "angle": angle, "is_mirror": False,
            "bid_count": count, "climbed_at": at}


def test_grades():
    assert v_grade_from_name("6c/V5") == "V5"
    assert v_grade_from_name("4a/VB") == "VB"
    assert v_grade_rank("VB") < v_grade_rank("V0") < v_grade_rank("V10")
    assert v_grade_rank(None) < v_grade_rank("VB")


def test_session_summary(tmp_path, board, climbs):
    store = Store(tmp_path / "app.sqlite3")
    sid = store.create_session("tension", "2026-11-08 18:00:00", "2026-11-08 19:30:00")
    a, b, c, d = climbs[:4]
    store.upsert_logbook({
        "ascents": [
            ascent("1", a, "2026-11-08 18:10:00", tries=1, difficulty=16),          # V6 flash
            ascent("2", b, "2026-11-08 18:40:00", tries=2, difficulty=18),          # V8 after attempts
            ascent("3", d, "2026-11-08 19:33:00", tries=1, difficulty=13),          # inside the grace period
            ascent("4", a, "2026-11-08 17:00:00", tries=1, difficulty=19),          # before the session
        ],
        "bids": [
            bid("5", b, "2026-11-08 18:20:00", count=3),                            # counts toward b
            bid("6", c, "2026-11-08 19:00:00", count=4),                            # project
        ],
    })
    session = store.session(sid)
    summary = summarize(session, store.entries_between(*session_window(session)), board)

    assert summary.send_count == 3
    assert summary.flash_count == 2
    assert summary.duration_seconds == 90 * 60
    by_uuid = {climb.climb_uuid: climb for climb in summary.climbs}
    assert by_uuid[b].tries_to_send == 5 and by_uuid[b].grade == "V8" and not by_uuid[b].flash
    assert by_uuid[c].sent is False and by_uuid[c].tries == 4
    assert summary.hardest.climb_uuid == b
    assert summary.pyramid() == [("V8", 1), ("V7", 0), ("V6", 1), ("V5", 0), ("V4", 0), ("V3", 1)]


def test_holds_and_mirror(board, climbs):
    mirror_layout = next(layout for layout in board.layouts() if layout.is_mirrored)
    climb = next(c for c in map(board.climb, climbs) if c.layout_id == mirror_layout.id)
    holds = board.holds(climb)
    mirrored = board.holds(climb, mirrored=True)
    assert holds and len(holds) == len(mirrored)
    assert all(abs(m.x + h.x) < 4 and abs(m.y - h.y) < 4 for m, h in zip(mirrored, holds))
    assert {h.role for h in holds} >= {"start", "finish"}


def test_tries_from_bid_count():
    from juglogger.summary import _tries
    # attempt_id 1 is Tension's "Flash", whatever bid_count says.
    assert _tries({"kind": "ascent", "attempt_id": 1, "bid_count": 3}) == 1
    # Otherwise bid_count is the count; attempt_id is an id, not a count.
    assert _tries({"kind": "ascent", "attempt_id": 2, "bid_count": 5}) == 5
    assert _tries({"kind": "ascent", "attempt_id": None, "bid_count": None}) == 1
    assert _tries({"kind": "ascent", "attempt_id": 2, "bid_count": "lots"}) == 1
    assert _tries({"kind": "bid", "attempt_id": 1, "bid_count": 4}) == 4


def test_tension_times_converted_from_utc():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from juglogger.store import utc_to_local
    from juglogger.tension import _to_local

    la = ZoneInfo("America/Los_Angeles")
    assert utc_to_local("2026-11-09 02:30:00.123456", la) == "2026-11-08 18:30:00"  # PST, UTC-8
    assert utc_to_local("2026-07-01 19:00:00", la) == "2026-07-01 12:00:00"         # PDT, UTC-7
    # Without a zone it uses this computer's.
    local = datetime(2026, 11, 9, 2, 30, tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    row = _to_local({"uuid": "x", "climbed_at": "2026-11-09 02:30:00", "created_at": None})
    assert row["climbed_at"] == local and row["created_at"] is None
