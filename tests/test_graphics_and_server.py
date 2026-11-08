import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from juglogger.server import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path, demo_mode=True))


def test_full_flow(client):
    status = client.get("/api/status").json()
    assert status["demo"] and status["account"]
    created = client.post("/api/sessions", json={
        "started_at": "2026-11-08T18:00", "ended_at": "2026-11-08T19:15"}).json()
    session = client.get(f"/api/sessions/{created['id']}?refresh=1").json()
    assert session["climb_count"] > 0 and session["duration"] == "1h 15m"

    for kind in ("stats", "pyramid", "list", "board"):
        for theme in ("light", "dark"):
            for crop in ("true", "false"):
                response = client.get(f"/api/sessions/{created['id']}/graphics/{kind}.png?theme={theme}&crop={crop}")
                assert response.status_code == 200, (kind, response.text)
                image = Image.open(io.BytesIO(response.content))
                assert image.mode == "RGBA"
                assert image.getpixel((0, 0))[3] == 0  # transparent: it's an overlay
                if crop == "false":
                    assert image.size == (1080, 1920)

    climb = session["climbs"][-1]["climb_uuid"]
    assert client.get(f"/api/sessions/{created['id']}/graphics/board.png?climb={climb}").status_code == 200


def test_timer_and_empty_session(client):
    started = client.post("/api/sessions/start").json()
    assert client.post("/api/sessions/start").json() == started  # one at a time
    stopped = client.post(f"/api/sessions/{started['id']}/stop").json()
    assert stopped["active"] is False
    for kind in ("stats", "pyramid", "list", "board"):
        assert client.get(f"/api/sessions/{started['id']}/graphics/{kind}.png").status_code == 200


def test_bad_times(client):
    assert client.post("/api/sessions", json={"started_at": "2026-11-08T19:00", "ended_at": "2026-11-08T18:00"}).status_code == 422
    assert client.post("/api/sessions", json={"started_at": "nope", "ended_at": "2026-11-08T18:00"}).status_code == 422
