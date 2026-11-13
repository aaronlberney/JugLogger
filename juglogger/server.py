"""JugLogger web app: a small FastAPI server with a single-page UI."""

from __future__ import annotations

import io
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import demo, tension
from .boarddb import BoardDB
from .graphics import GRAPHICS, render_graphic
from .graphics.board import BoardRenderer, asset_name
from .profile import build_profile
from .store import Store, now, normalize_time
from .summary import session_window, summarize

BOARD = tension.BOARD
WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FETCH_EVERY_SECONDS = 20
FAVORITE_COLOR = (53, 208, 127)        # the favorite hold's outline on the profile board
LEAST_FAVORITE_COLOR = (214, 40, 40)   # the least favorite's
HOLD_KEYS = {"favorite_hold": FAVORITE_COLOR, "least_favorite_hold": LEAST_FAVORITE_COLOR}


@dataclass
class Context:
    data_dir: Path
    demo: bool
    grade_source: str = "logged"
    store: Store = field(init=False)
    board_db_path: Path = field(init=False)
    last_fetch: float = 0.0
    last_error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.store = Store(self.data_dir / ("demo.sqlite3" if self.demo else "juglogger.sqlite3"))
        if self.demo:
            self.board_db_path = demo.build(self.data_dir)
            if not self.store.account(BOARD):
                self.store.save_account(BOARD, "demo", 0, "demo-token")
        else:
            self.board_db_path = self.data_dir / "tension.sqlite3"
        self._renderer = None
        self._synced_for_profile = False

    @property
    def board_ready(self) -> bool:
        return self.board_db_path.exists()

    def board(self) -> BoardDB:
        if not self.board_ready:
            raise HTTPException(409, "The Tension board database is missing. Run: python -m juglogger setup")
        return BoardDB(self.board_db_path)

    def renderer(self) -> BoardRenderer:
        # Kept between requests so each layout is calibrated only once.
        if self._renderer is None:
            self._renderer = BoardRenderer(self.board())
        return self._renderer

    def fetch(self, force: bool = False) -> None:
        """Pull your logbook from Tension, at most every FETCH_EVERY_SECONDS."""
        account = self.store.account(BOARD)
        if not account:
            return
        with self.lock:
            if not force and time.time() - self.last_fetch < FETCH_EVERY_SECONDS:
                return
            self.last_fetch = time.time()
            try:
                if self.demo:
                    logbook = demo.DemoLogbook(self.board_db_path, self.store).fetch_logbook()
                else:
                    logbook = tension.fetch_logbook(account["token"])
                self.store.upsert_logbook(logbook)
                self.last_error = None
            except tension.AuthError as error:
                self.last_error = str(error)
                self.store.delete_account(BOARD)
            except Exception as error:  # network trouble: keep the last copy
                self.last_error = f"Couldn't reach Tension: {error}"

    def sync_missing_climbs(self, climb_uuids: set[str]) -> None:
        """Climbs set after the board database was downloaded aren't in it yet."""
        if self.demo or not climb_uuids:
            return
        board = self.board()
        if all(board.climb(uuid) for uuid in climb_uuids):
            return
        account = self.store.account(BOARD)
        try:
            tension.sync_board_database(self.board_db_path, account["token"] if account else None)
        except Exception as error:
            self.last_error = f"Couldn't update the board database: {error}"

    def summary(self, session: dict):
        start, end = session_window(session)
        entries = self.store.entries_between(start, end)
        self.sync_missing_climbs({e["climb_uuid"] for e in entries})
        return summarize(session, entries, self.board(), grade_source=self.grade_source)


    # Profile

    def favorites(self) -> dict:
        saved = json.loads(self.store.meta("profile_favorites") or "{}")
        return {key: saved.get(key) for key in (*HOLD_KEYS, "favorite_angle")}

    def profile(self) -> dict:
        account = self.store.account(BOARD)
        entries = self.store.listed_entries()
        if not self._synced_for_profile:   # once per run: a deleted climb never appears
            self._synced_for_profile = True
            self.sync_missing_climbs({e["climb_uuid"] for e in entries if e["kind"] == "ascent"})
        board = self.board()
        set_climbs = board.climbs_set_by(account["user_id"], account["username"]) if account else []
        payload = build_profile(entries, board, grade_source=self.grade_source, set_climbs=set_climbs)
        sends: dict[int, int] = {}
        for ascent in payload["ascents"]:
            sends[ascent["layout_id"]] = sends.get(ascent["layout_id"], 0) + 1
        renderer = self.renderer()
        hold_use = self.hold_use()
        layouts = []
        for layout in board.layouts():
            holes = renderer.hole_positions(layout.id)
            if not holes:
                continue
            use = hold_use.get(layout.id, {})
            layouts.append({
                "id": layout.id,
                "name": layout.name,
                "short": (asset_name(layout) or "").split("-")[1].title(),
                "ascents": sends.get(layout.id, 0),
                "holds_used": len(use),
                "max_use": max(use.values(), default=0),
                # x, y, where it is in the image (fractions), climbs sent that use it
                "holes": [[x, y, round(fx, 4), round(fy, 4), use.get((x, y), 0)] for x, y, fx, fy in holes],
            })
        layouts.sort(key=lambda item: -item["ascents"])
        payload.update(
            username=account["username"] if account else None,
            layouts=layouts,
            favorites=self.favorites(),
            last_fetch_at=self.store.meta("last_fetch_at"),
            error=self.last_error,
        )
        return payload

    def warm(self) -> None:
        """Find the shape of every hold on a climb you've sent, in the background.
        The first time takes several seconds per layout; doing it at startup means
        the profile heatmap doesn't have to wait for it."""
        def work():
            try:
                renderer = self.renderer()
                for layout_id, use in self.hold_use().items():
                    for x, y in use:
                        renderer.hold_shape(layout_id, x, y)
            except Exception:   # no board data yet, say: the page reports that itself
                pass
        if self.board_ready:
            threading.Thread(target=work, daemon=True).start()

    def hold_use(self) -> dict[int, dict[tuple[float, float], int]]:
        """Per layout, how many of the climbs ever sent use each hold."""
        sent = {(e["climb_uuid"], bool(e["is_mirror"]))
                for e in self.store.listed_entries() if e["kind"] == "ascent"}
        return self.renderer().hold_use_by_layout(sent)


def _png(image) -> Response:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


class ConnectBody(BaseModel):
    username: str
    password: str


class SessionBody(BaseModel):
    started_at: str | None = None
    ended_at: str | None = None


def create_app(data_dir: Path | None = None, demo_mode: bool | None = None) -> FastAPI:
    if demo_mode is None:
        demo_mode = os.environ.get("JUGLOGGER_DEMO", "") not in ("", "0", "false")
    data_dir = Path(data_dir or os.environ.get("JUGLOGGER_DATA") or DEFAULT_DATA_DIR)
    ctx = Context(
        data_dir=data_dir,
        demo=demo_mode,
        grade_source=os.environ.get("JUGLOGGER_GRADES", "logged"),
    )
    app = FastAPI(title="JugLogger")
    app.state.ctx = ctx

    def get_session(session_id: int) -> dict:
        session = ctx.store.session(session_id)
        if not session:
            raise HTTPException(404, "No such session")
        return session

    def session_payload(session: dict) -> dict:
        payload = ctx.summary(session).to_dict()
        payload.update(active=session["ended_at"] is None, now=now(),
                       last_fetch_at=ctx.store.meta("last_fetch_at"), error=ctx.last_error)
        return payload

    @app.get("/api/status")
    def status():
        account = ctx.store.account(BOARD)
        active = ctx.store.active_session()
        return {
            "demo": ctx.demo,
            "board_ready": ctx.board_ready,
            "account": {"username": account["username"]} if account else None,
            "active_session_id": active["id"] if active else None,
            "error": ctx.last_error,
            "graphics": [{"kind": k, "title": title} for k, (title, _) in GRAPHICS.items()],
        }

    @app.post("/api/connect")
    def connect(body: ConnectBody):
        if ctx.demo:
            ctx.store.save_account(BOARD, body.username or "demo", 0, "demo-token")
            return {"ok": True}
        try:
            session = tension.login(body.username, body.password)
        except tension.AuthError as error:
            raise HTTPException(401, str(error))
        except Exception as error:
            raise HTTPException(502, f"Couldn't reach Tension: {error}")
        ctx.store.save_account(BOARD, body.username, session["user_id"], session["token"])
        ctx.last_error = None
        ctx.fetch(force=True)
        return {"ok": True}

    @app.post("/api/disconnect")
    def disconnect():
        ctx.store.delete_account(BOARD)
        return {"ok": True}

    @app.get("/api/sessions")
    def list_sessions():
        out = []
        for session in ctx.store.sessions():
            summary = ctx.summary(session)
            hardest = summary.hardest
            out.append({
                **session,
                "active": session["ended_at"] is None,
                "duration_seconds": summary.duration_seconds,
                "send_count": summary.send_count,
                "climb_count": len(summary.climbs),
                "top_grade": hardest.grade if hardest else None,
            })
        return out

    @app.post("/api/sessions/start")
    def start_session():
        if not ctx.store.account(BOARD):
            raise HTTPException(409, "Connect your Tension account first.")
        active = ctx.store.active_session()
        if active:
            return {"id": active["id"]}
        ctx.fetch(force=True)
        return {"id": ctx.store.start_session(BOARD)}

    @app.post("/api/sessions")
    def create_session(body: SessionBody):
        """Make a session for a past window, e.g. to try the graphics on old climbs."""
        if not body.started_at or not body.ended_at:
            raise HTTPException(422, "started_at and ended_at are required")
        try:
            if normalize_time(body.ended_at) <= normalize_time(body.started_at):
                raise HTTPException(422, "The end must be after the start.")
            session_id = ctx.store.create_session(BOARD, body.started_at, body.ended_at)
        except ValueError as error:
            raise HTTPException(422, str(error))
        ctx.fetch(force=True)
        return {"id": session_id}

    @app.post("/api/sessions/{session_id}/stop")
    def stop_session(session_id: int):
        session = get_session(session_id)
        if session["ended_at"] is None:
            ctx.store.stop_session(session_id)
        ctx.fetch(force=True)
        return session_payload(get_session(session_id))

    @app.get("/api/sessions/{session_id}")
    def read_session(session_id: int, refresh: bool = False):
        session = get_session(session_id)
        if refresh or session["ended_at"] is None:
            ctx.fetch(force=refresh)
        return session_payload(session)

    @app.patch("/api/sessions/{session_id}")
    def edit_session(session_id: int, body: SessionBody):
        get_session(session_id)
        try:
            ctx.store.update_session(session_id, body.started_at, body.ended_at)
        except ValueError as error:
            raise HTTPException(422, str(error))
        return session_payload(get_session(session_id))

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: int):
        get_session(session_id)
        ctx.store.delete_session(session_id)
        return {"ok": True}

    @app.get("/api/sessions/{session_id}/graphics/{kind}.png")
    def graphic(session_id: int, kind: str, theme: str = Query("light", pattern="^(light|dark)$"),
                crop: bool = True, climb: str | None = None, download: bool = False,
                holds: str = Query("all", pattern="^(all|lit)$")):
        if kind not in GRAPHICS:
            raise HTTPException(404, "No such graphic")
        session = get_session(session_id)
        png = render_graphic(kind, ctx.summary(session), theme=theme, crop=crop,
                             board=ctx.renderer() if kind in ("board", "heatmap") else None, climb_uuid=climb,
                             lit_only=holds == "lit")
        headers = {"Cache-Control": "no-store"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="juglogger-{session_id}-{kind}.png"'
        return Response(png, media_type="image/png", headers=headers)

    @app.get("/api/profile")
    def profile(refresh: bool = False):
        if not ctx.store.account(BOARD):
            raise HTTPException(409, "Connect your Tension account first.")
        ctx.fetch(force=refresh)
        return ctx.profile()

    @app.put("/api/profile/favorites")
    def save_favorites(body: dict):
        """Any of favorite_hold, least_favorite_hold ({layout_id, x, y}) and
        favorite_angle (degrees); null clears one."""
        favorites = ctx.favorites()
        for key, value in body.items():
            if key not in favorites:
                raise HTTPException(422, f"Unknown favorite: {key}")
            if value is None:
                favorites[key] = None
            elif key == "favorite_angle":
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 90:
                    raise HTTPException(422, "The angle must be a whole number of degrees, 0 to 90.")
                favorites[key] = value
            else:
                try:
                    layout_id, x, y = int(value["layout_id"]), float(value["x"]), float(value["y"])
                except (KeyError, TypeError, ValueError):
                    raise HTTPException(422, "A hold needs layout_id, x and y.")
                if not any(hx == x and hy == y for hx, hy, _, _ in ctx.renderer().hole_positions(layout_id)):
                    raise HTTPException(422, "There's no hold there on that layout.")
                favorites[key] = {"layout_id": layout_id, "x": x, "y": y}
        ctx.store.set_meta("profile_favorites", json.dumps(favorites))
        return favorites

    @app.get("/api/profile/board/{layout_id}.png")
    def profile_board(layout_id: int):
        """The board with the favorite and least favorite holds outlined."""
        favorites = ctx.favorites()
        marks = [(hold["x"], hold["y"], color) for key, color in HOLD_KEYS.items()
                 if (hold := favorites[key]) and hold["layout_id"] == layout_id]
        image = ctx.renderer().render_marked(layout_id, marks)
        if image is None:
            raise HTTPException(404, "No such layout")
        return _png(image)

    @app.get("/api/profile/heatmap/{layout_id}.png")
    def profile_heatmap(layout_id: int):
        """Every hold on a climb you've sent, darker the more climbs use it."""
        image = ctx.renderer().render_profile_heatmap(layout_id, ctx.hold_use().get(layout_id, {}))
        if image is None:
            raise HTTPException(404, "No such layout")
        return _png(image)

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    app.mount("/fonts", StaticFiles(directory=WEB_DIR.parent / "fonts"), name="fonts")

    @app.get("/")
    def index():
        # The script and stylesheet are stamped with their modified time and the page
        # itself is never cached, so a browser always shows the current UI.
        html = (WEB_DIR / "index.html").read_text()
        for name in ("app.js", "app.css"):
            html = html.replace(f"/static/{name}", f"/static/{name}?v={int((WEB_DIR / name).stat().st_mtime)}")
        return Response(html, media_type="text/html", headers={"Cache-Control": "no-store"})

    return app
