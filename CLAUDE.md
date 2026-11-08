# JugLogger: context for Claude sessions

JugLogger is Strava for board climbing, for Aaron's personal use (not public). He starts a timer, climbs on a **Tension Board 2** while logging sends and attempts in the official Tension app, then stops the timer. JugLogger pulls the climbs logged in that window from his Tension account (via [BoardLib](https://github.com/lemeryfertitta/BoardLib), an unofficial API) and turns the session into **transparent overlay graphics** to share on Instagram, like Strava's stickers.

README.md has the user-facing setup and usage. This file holds the decisions and current status.

## Current status (handoff from the first build session, Nov 2026)

- The MVP is built and pushed: timer, live climb list, session summary, four share graphics, history, demo mode, CLI (`setup`, `doctor`, `serve`). 10 tests pass (`pytest`).
- **It has never run against Aaron's real Tension account.** It was built in a cloud sandbox that couldn't reach Tension's servers, so the board database code and calibration were written against Tension's data format but only tested on fake data.
- **Next step: get it running on Aaron's computer.** The first step is installing Python. Prefer **uv** (no admin password needed): `curl -LsSf https://astral.sh/uv/install.sh | sh`, then `uv python install 3.14`, then create a venv with that Python and run `pip install -e ".[dev]"`. Python 3.14 is preferred over 3.15, since some dependencies (pandas via boardlib, numpy, Pillow) may not have 3.15 wheels yet.
- Then follow the testing plan below. **Aaron must type his Tension password himself** (`python -m juglogger setup` prompts for it). Never ask for it in chat or put it in a file.

## Testing plan (walk Aaron through this)

1. Optional: `python -m juglogger serve --demo` shows fake climbs on the real TB2 layouts. Press Start, and a climb appears every 45s.
2. `python -m juglogger setup` signs in (keeps only a token in `data/`) and downloads Tension's climb database (via BoardLib, from an APK mirror; can take minutes).
3. `python -m juglogger doctor`. Review its output **together with** `data/calibration-mirror.png` and `data/calibration-spray.png`. Check that:
   - the TB2 Mirror and Spray layouts and the 12×12 size were found
   - the hold calibration matched at least 85% of holds (every red ring sits on a hold in the preview images)
   - the `climbed_at` times of the latest entries match the computer's local time. Session windows compare `climbed_at` as local wall-clock time. If it turns out to be UTC, `summary.session_window` / `store.entries_between` need an offset.
4. `python -m juglogger serve`, then **Log a past session** around an old session: check that the climbs, grades, tries and flashes are right.
5. Live test: press Start, log 4–6 climbs in the Tension app (a flash, a send after several tries, an attempts-only climb, a mirrored climb on Mirror), check that they appear within about 20s, press Stop, and check the graphics.

Things most likely to need fixing on first contact with real data: product-size/layout name matching in `boarddb.py` (`SIZE_12X12_RE`, `LAYOUT_KEYWORDS`), the `climbed_at` timezone, `attempt_id` vs `bid_count` meaning for tries (`summary._tries`), and calibration quality.

## Design decisions (Aaron's preferences, keep these)

- **Every graphic is a transparent PNG overlay**, Strava-style: no background, ever. "Sticker" format = cropped to content; "Story" = full 1080×1920 frame.
- **Accent color: dark red `#D62828`**. Earlier it was Strava orange, then a brighter red (#FF3B30); Aaron chose this darker red.
- **Fonts: Archivo, matching aaronberney.com.** Condensed Archivo (wdth 62, static instances in `juglogger/fonts/ArchivoCondensed-*.ttf`) for big type: values, names, grades, wordmark, timer. Regular Archivo for labels and small text. No monospace.
- Aesthetic: sleek, minimal, "tech bro" Instagram, dark UI.
- App name/logo: **JUGLOGGER** wordmark with a red jug-hold mark (a bowl shape), present on every graphic.
- **Board:** TB2 **Mirror and Spray, 12×12 only**. Board angle must **not** change the board graphic. Angle is just listed per climb.
- **Board graphic uses Aaron's TB2 layout images**, made colorless with transparent backgrounds (`juglogger/assets/`, built from `assets/source/` by `scripts/build_layout_assets.py`). The climb's holds are ringed in Tension's role colors (start/hand/finish/foot); unlit holds are faded. There's an **All holds / Lit only** toggle (`holds=all|lit`).
- **Grade pyramid:** rows left-aligned on a shared start line, slim blocks (half as tall as wide), tight gaps. The hardest grade is on top.
- The four graphics: stats (sends / top grade / time + mini pyramid), grade pyramid, climb list, hardest send (on the board).
- Open idea Aaron hasn't decided on: lowercase labels to match his website ("sends", "top grade", "juglogger").

## Future ideas (not started)

More boards (Kilter via its new API, Moonboard), phone use at the gym (hosting or a PWA), progress over time, feed and social features, more graphics.

## Code map

- `juglogger/__main__.py`: CLI. `server.py`: FastAPI app plus JSON API; the UI is `web/` (plain HTML/CSS/JS, no build step).
- `tension.py`: login, logbook (ascents = sends, bids = failed attempts), and shared DB sync, all through BoardLib.
- `boarddb.py`: reads the Tension SQLite DB (climbs, grades, holds, mirrored holes).
- `store.py`: JugLogger's own SQLite in `data/` (account token, sessions, logbook copy). `data/` is gitignored.
- `summary.py`: session window → summary. A climb = climb + angle + mirror; tries-to-send counts bids logged before the first send; there's a 5-minute grace period after stop.
- `graphics/render.py`: the overlays (Pillow, drawn at 2x). `graphics/board.py`: board rendering. `graphics/calibrate.py`: fits DB hole coordinates to the hold centers in the layout images.
- `demo.py`: fake TB2 DB (holes from the real layout images) plus a fake logbook for demo mode and tests.

## Conventions

- Run `pytest` before committing. Commit and push to `main` (personal repo).
- When changing a graphic, render a preview and look at it on both a dark photo and a bright one before calling it done.
