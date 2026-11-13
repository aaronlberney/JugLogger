# JugLogger: context for Claude sessions

JugLogger is Strava for board climbing, for Aaron's personal use (not public). He starts a timer, climbs on a **Tension Board 2** while logging sends and attempts in the official Tension app, then stops the timer. JugLogger pulls the climbs logged in that window from his Tension account (via [BoardLib](https://github.com/lemeryfertitta/BoardLib), an unofficial API) and turns the session into **transparent overlay graphics** to share on Instagram, like Strava's stickers.

README.md has the user-facing setup and usage. This file holds the decisions and current status.

## Current status (handoff from the first build session, Nov 2026)

- The MVP is built and pushed: timer, live climb list, session summary, four share graphics, history, demo mode, CLI (`setup`, `doctor`, `serve`). tests pass (`pytest`).
- **It has never run against Aaron's real Tension account.** It was built in a cloud sandbox that couldn't reach Tension's servers, so the board database code and calibration were written against Tension's data format but only tested on fake data.
- **Next step: get it running on Aaron's computer.** The first step is installing Python. Prefer **uv** (no admin password needed): `curl -LsSf https://astral.sh/uv/install.sh | sh`, then `uv python install 3.14`, then create a venv with that Python and run `pip install -e ".[dev]"`. Python 3.14 is preferred over 3.15, since some dependencies (pandas via boardlib, numpy, Pillow) may not have 3.15 wheels yet.
- Then follow the testing plan below. **Aaron must type his Tension password himself** (`python -m juglogger setup` prompts for it). Never ask for it in chat or put it in a file.

## Testing plan (walk Aaron through this)

1. Optional: `python -m juglogger serve --demo` shows fake climbs on the real TB2 layouts. Press Start, and a climb appears every 45s.
2. `python -m juglogger setup` signs in (keeps only a token in `data/`) and downloads Tension's climb database (via BoardLib, from an APK mirror; can take minutes).
3. `python -m juglogger doctor`. Review its output **together with** `data/calibration-mirror.png` and `data/calibration-spray.png`. Check that:
   - the TB2 Mirror and Spray layouts and the 12×12 size were found
   - the hold calibration matched at least 85% of holds (every red ring sits on a hold in the preview images)
   - the `climbed_at` times of the latest entries match the computer's local time. The API sends UTC with no zone attached (Boardsesh, which syncs real Tension accounts, treats it as UTC), so `tension._to_local` converts it to local time at import and everything after that works in local wall-clock time. If doctor's times are off by whole hours, that assumption is wrong for this account: remove the conversion in `tension.fetch_logbook`.
4. `python -m juglogger serve`, then **Log a past session** around an old session: check that the climbs, grades, tries and flashes are right.
5. Live test: press Start, log 4–6 climbs in the Tension app (a flash, a send after several tries, an attempts-only climb, a mirrored climb on Mirror), check that they appear within about 20s, press Stop, and check the graphics.

Things most likely to need fixing on first contact with real data: product-size/layout name matching in `boarddb.py` (`SIZE_12X12_RE`, `LAYOUT_KEYWORDS`), the `climbed_at` timezone (now assumed UTC, see step 3), tries (`summary._tries` now uses `bid_count` as the count and `attempt_id == 1` as flash, matching Boardsesh), and calibration quality.

## Design decisions (Aaron's preferences, keep these)

- **Every graphic is a transparent PNG overlay**, Strava-style: no background, ever. "Sticker" format = cropped to content; "Story" = full 1080×1920 frame.
- **Accent color: dark red `#D62828`**. Earlier it was Strava orange, then a brighter red (#FF3B30); Aaron chose this darker red.
- **Fonts: Archivo, matching aaronberney.com.** Condensed Archivo (wdth 62, static instances in `juglogger/fonts/ArchivoCondensed-*.ttf`) for big type: values, names, grades, wordmark, timer. Regular Archivo for labels and small text. No monospace.
- Aesthetic: sleek, minimal, "tech bro" Instagram, dark UI.
- App name/logo: **JUGLOGGER** wordmark with a red jug-hold mark (a bowl shape), present on every graphic.
- **Board:** TB2 **Mirror and Spray, 12×12 only**. Board angle must **not** change the board graphic. Angle is just listed per climb.
- **Board graphic uses Aaron's TB2 hold renders** (holds on black, in `juglogger/assets/source/*.jpg`), kept in their **real colors** (gray, yellow, pink) on a transparent background, built into `juglogger/assets/` by `scripts/build_layout_assets.py`. Aaron chose these over the earlier colorless version because the holds were hard to see on photos. The climb's holds are **outlined along their own shape** with a glowing line in Tension's role colors (start/hand/finish/foot), not circled. The glow is **outside the hold only** (Aaron's choice), so lit holds keep their own color. Unlit holds are faded and get a thin dark rim so they still read on bright or busy photos (stronger under the black-type theme). There's an **All holds / Lit only** toggle (`holds=all|lit`). Touching holds are separated automatically; where that goes wrong, Aaron draws bright cyan lines on `data/outlines-<layout>.png` (written by `doctor`) and `scripts/import_hold_cuts.py` saves them as `juglogger/assets/tb2-<layout>-12x12-cuts.png` (Spray has four such fixes; Mirror has none). After changing the source images, rerun the build script and `python -m juglogger doctor` to check the calibration previews.
- **Grade pyramid:** rows left-aligned on a shared start line, slim blocks (half as tall as wide), tight gaps. The hardest grade is on top.
- The four graphics: stats (sends / top grade / time + mini pyramid), grade pyramid, climb list, hardest send (on the board).
- A fifth graphic, the **hold heatmap**: the stats layout (date, sends, top grade, time, plus a holds-used count, no pyramid) set over the board showing only the holds used in the session. Each hold is a flat, fully opaque fill: accent red for one use, darkening to oxblood at 10 or more. The scale is **fixed** (`heat_color` in `graphics/board.py`), so a shade means the same count in every session. No legend bar, no rim. Holds are cut back a few pixels around the type so it stays readable. A hold counts once per climb it appears in.
- **Profile tab** (`#/profile`), modeled on the Tension app's own profile: username, all-time totals, a grade pyramid counting **every ascent** (repeats included; click a bar to filter), a favorite-angle picker, favorite and least favorite hold (click a hold on the board image; outlined along its shape, green for favorite and red for least), an all-time heatmap per layout, and the logbook grouped by date with grade, angle, classic and date filters. "Classic" is Tension's benchmark flag. The profile heatmap is a page element, not a share overlay, so it breaks the overlay heatmap's rules on purpose: the scale runs light orange for one use, darkening to deep red for the most used (Aaron's choice: darker = more use; it stops short of oxblood, which would vanish on the dark page), is relative to that layout's most-used hold, and has a legend. Favorites are saved in the `meta` table (`profile_favorites`). The logbook shows 10 ascents at a time with a "Show 10 more" button, and Badges show one line until "Show all" (both Aaron's requests). Hovering a heatmap hold shows how many sent climbs use it. The favorite angle is deliberately small (Aaron asked for it "way smaller"). **Climbs set**: the owner's published climbs from the board database (matched on `setter_id`), with grade and ascents at each angle. **Badges** are JugLogger's own, computed from the logbook in `profile.badges` (Tension has none); tiered ones show progress to the next step. BoardLib has no leaderboard call (checked Nov 2026): only login, sync, explore, users/{id}, followers/followees, notifications and saves.
- Open idea Aaron hasn't decided on: lowercase labels to match his website ("sends", "top grade", "juglogger").

## Future ideas (not started)

More boards (Kilter via its new API, Moonboard), phone use at the gym (hosting or a PWA), progress over time, feed and social features, more graphics.

**Outdoor climbing (Aaron's afterthought, Nov 2026, maybe later):** Mountain Project's public API was shut down around 2020 (after onX bought it) and scraping breaks its terms. The way in is the **tick CSV export** from Aaron's MP profile (date, route, grade, style, route type, pitches, length, location, URL, notes). Use **OpenBeta** (openbeta.io, free CC0 GraphQL API) for route and crag details and GPS. theCrag has an API but you have to apply for access. Ticks have a date but no time, so an outdoor "session" would be a crag day (one date, one area), not a timer window. There's no board graphic, and YDS/V grades need their own pyramid scales.

## Code map

- `juglogger/__main__.py`: CLI. `server.py`: FastAPI app plus JSON API; the UI is `web/` (plain HTML/CSS/JS, no build step).
- `tension.py`: login, logbook (ascents = sends, bids = failed attempts), and shared DB sync, all through BoardLib.
- `boarddb.py`: reads the Tension SQLite DB (climbs, grades, holds, mirrored holes).
- `store.py`: JugLogger's own SQLite in `data/` (account token, sessions, logbook copy). `data/` is gitignored.
- `profile.py`: the whole logbook → profile (ascents, totals, pyramid, per-angle stats) and the logbook filters.
- `summary.py`: session window → summary. A climb = climb + angle + mirror; tries-to-send counts bids logged before the first send; there's a 5-minute grace period after stop.
- `graphics/render.py`: the overlays (Pillow, drawn at 2x). `graphics/board.py`: board rendering. `graphics/calibrate.py`: fits DB hole coordinates to the hold centers in the layout images.
- `demo.py`: fake TB2 DB (holes from the real layout images) plus a fake logbook for demo mode and tests.

## Conventions

- Run `pytest` before committing. Commit and push to `main` (personal repo).
- When changing a graphic, render a preview and look at it on both a dark photo and a bright one before calling it done.
