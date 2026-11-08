"""JugLogger web app: a small FastAPI server with a single-page UI."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import demo, tension
from .boarddb import BoardDB
from .graphics import GRAPHICS, render_graphic
from .graphics.board import BoardRenderer
from .store import Store, now, normalize_time
from .summary import session_window, summarize

BOARD = tension.BOARD
WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FETCH_EVERY_SECONDS = 20


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
                             board=ctx.renderer() if kind == "board" else None, climb_uuid=climb,
                             lit_only=holds == "lit")
        headers = {"Cache-Control": "no-store"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="juglogger-{session_id}-{kind}.png"'
        return Response(png, media_type="image/png", headers=headers)

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    app.mount("/fonts", StaticFiles(directory=WEB_DIR.parent / "fonts"), name="fonts")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    return app
