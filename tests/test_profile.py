import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from juglogger.profile import build_profile, filter_ascents
from juglogger.server import create_app


def entry(uuid, kind, climb, at, angle=40, tries=1, difficulty=14, mirror=False):
    return {"uuid": uuid, "kind": kind, "climb_uuid": climb, "angle": angle, "is_mirror": int(mirror),
            "bid_count": tries, "attempt_id": tries, "difficulty": difficulty if kind == "ascent" else None,
            "is_benchmark": 0, "comment": "", "climbed_at": at}


def test_profile_counts_every_ascent(board, climbs):
    a, b, c = climbs[:3]
    profile = build_profile([
        entry("1", "ascent", a, "2026-10-01 18:00:00"),                      # flash
        entry("2", "bid", b, "2026-10-01 18:10:00", tries=3),
        entry("3", "ascent", b, "2026-10-01 18:20:00", difficulty=16),       # one try, but after bids
        entry("4", "ascent", a, "2026-10-09 18:00:00"),                      # a repeat
        entry("5", "ascent", c, "2026-10-09 18:30:00", angle=30, tries=4, difficulty=12),
    ], board)
    assert profile["totals"] == {"ascents": 4, "climbs": 3, "flashes": 1, "top_grade": "V6", "days": 2}
    assert [a["uuid"] for a in profile["ascents"]] == ["5", "4", "3", "1"]   # newest first
    assert profile["pyramid"] == [("V6", 1), ("V5", 0), ("V4", 2), ("V3", 0), ("V2", 1)]
    assert profile["angles"] == [{"angle": 30, "ascents": 1, "top_grade": "V2"},
                                 {"angle": 40, "ascents": 3, "top_grade": "V6"}]

    ascents = profile["ascents"]
    assert [a["uuid"] for a in filter_ascents(ascents, grade="V4")] == ["4", "1"]
    assert [a["uuid"] for a in filter_ascents(ascents, angle=30)] == ["5"]
    assert [a["uuid"] for a in filter_ascents(ascents, date_from="2026-10-02")] == ["5", "4"]
    assert [a["uuid"] for a in filter_ascents(ascents, date_to="2026-10-01")] == ["3", "1"]
    earned = {b["key"]: b for b in profile["badges"]}
    assert earned["ascents"]["earned"] and earned["ascents"]["progress"] == {"value": 4, "next": 10}
    assert earned["top_grade"]["mark"] == "V6" and earned["hardest_flash"]["mark"] == "V4"
    assert not earned["double_digits"]["earned"] and not earned["setter"]["earned"]

    classics = filter_ascents(ascents, classic="classic")
    assert all(a["is_classic"] for a in classics)
    assert len(classics) + len(filter_ascents(ascents, classic="other")) == 4


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path, demo_mode=True))


def test_profile_api(client):
    client.post("/api/sessions", json={"started_at": "2026-11-08T18:00", "ended_at": "2026-11-08T19:15"})
    profile = client.get("/api/profile?refresh=1").json()
    assert profile["username"] == "demo" and profile["totals"]["ascents"] > 0
    assert sum(count for _, count in profile["pyramid"]) == profile["totals"]["ascents"]
    assert profile["favorites"] == {"favorite_hold": None, "least_favorite_hold": None, "favorite_angle": None}

    layout = profile["layouts"][0]
    x, y = layout["holes"][0][:2]
    hold = {"layout_id": layout["id"], "x": x, "y": y}
    saved = client.put("/api/profile/favorites", json={"favorite_hold": hold, "favorite_angle": 40}).json()
    assert saved["favorite_hold"] == hold and saved["favorite_angle"] == 40
    assert client.get("/api/profile").json()["favorites"] == saved
    assert client.put("/api/profile/favorites", json={"favorite_angle": None}).json()["favorite_angle"] is None
    assert client.put("/api/profile/favorites", json={"favorite_hold": {**hold, "x": 9999}}).status_code == 422
    assert client.put("/api/profile/favorites", json={"favorite_angle": "steep"}).status_code == 422
    assert client.put("/api/profile/favorites", json={"shoes": 1}).status_code == 422

    for kind in ("board", "heatmap"):
        response = client.get(f"/api/profile/{kind}/{layout['id']}.png")
        assert response.status_code == 200
        image = Image.open(io.BytesIO(response.content))
        assert image.mode == "RGBA" and image.getpixel((0, 0))[3] == 0
    assert layout["holds_used"] > 0
    counts = [hole[4] for hole in layout["holes"]]   # climbs sent that use each hold
    assert sum(1 for n in counts if n) == layout["holds_used"] and max(counts) == layout["max_use"]
    assert client.get("/api/profile/board/999.png").status_code == 404


def test_set_climbs_and_setter_badges(board):
    mine = board.climbs_set_by(None, "AARON")   # the demo database has usernames only
    assert mine and board.climbs_set_by(None, "nobody") == [] and board.climbs_set_by(None, None) == []
    for climb in mine:
        assert climb["ascents"] == sum(a["ascents"] for a in climb["angles"])
        assert climb["angles"] and all(a["angle"] for a in climb["angles"])
    profile = build_profile([], board, set_climbs=mine)
    assert profile["set_climbs"] == mine
    earned = {b["key"]: b for b in profile["badges"]}
    assert earned["setter"]["earned"] and earned["repeats"]["earned"] and not earned["ascents"]["earned"]
