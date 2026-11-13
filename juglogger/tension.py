"""Tension Board API access, through BoardLib.

Only the calls JugLogger needs: sign in, download your sends ("ascents") and failed
attempts ("bids"), and pull new public climbs into the local board database.
"""

from __future__ import annotations

from pathlib import Path

import requests

from .store import utc_to_local

BOARD = "tension"
BASE_SYNC_DATE = "1970-01-01 00:00:00.000000"


class AuthError(Exception):
    """The username/password or saved token was rejected."""


def login(username: str, password: str) -> dict:
    """Returns {"token": ..., "user_id": ...}."""
    import boardlib.api.aurora as aurora

    try:
        session = aurora.login(BOARD, username, password)
    except ValueError as error:
        raise AuthError(str(error)) from error
    return {"token": session["token"], "user_id": session["user_id"]}


def fetch_logbook(token: str) -> dict[str, list[dict]]:
    """All of your ascents and bids, as the raw rows the API returns."""
    import boardlib.api.aurora as aurora

    ascents, bids = [], []
    try:
        for page in aurora.sync(BOARD, {"ascents": BASE_SYNC_DATE, "bids": BASE_SYNC_DATE}, token):
            ascents.extend(page.get("ascents", []))
            bids.extend(page.get("bids", []))
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code in (401, 403):
            raise AuthError("Your Tension session expired. Sign in again.") from error
        raise
    return {"ascents": [_to_local(row) for row in ascents], "bids": [_to_local(row) for row in bids]}


def _to_local(row: dict) -> dict:
    """The API's times are UTC with no zone attached ("2026-11-08 23:02:11"). The rest
    of JugLogger works in local wall-clock time, so convert them here, once. (Boardsesh,
    which syncs real Tension accounts, reads these times as UTC too.)"""
    row = dict(row)
    for key in ("climbed_at", "created_at"):
        if row.get(key):
            row[key] = utc_to_local(row[key])
    return row


def sync_board_database(database_path: Path, token: str | None) -> dict[str, int]:
    """Bring the public tables (climbs, grades, stats) up to date, so climbs set after
    the database was downloaded have names, grades and holds."""
    import boardlib.api.aurora as aurora
    import boardlib.db.aurora as aurora_db

    totals: dict[str, int] = {}
    sync_dates = aurora_db.get_shared_syncs(str(database_path))
    for page in aurora.sync(BOARD, sync_dates, token=token):
        for table, count in aurora_db.sync_shared_tables(str(database_path), page).items():
            totals[table] = totals.get(table, 0) + count
    return totals
