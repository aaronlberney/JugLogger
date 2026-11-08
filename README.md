# JugLogger

Strava for board climbing. Start a timer, climb on the Tension Board 2 and log your sends and attempts in the Tension app as usual, then stop the timer. JugLogger pulls the climbs you logged during that window from your Tension account and turns the session into share graphics. Each graphic is a transparent overlay you can drop onto a photo or video, like Strava's stickers.

**Status:** personal MVP. Tension Board 2 only (Mirror and Spray layouts, 12×12), running on your own computer. It uses the unofficial Tension API through [BoardLib](https://github.com/lemeryfertitta/BoardLib).

## Graphics

All are transparent PNGs with white (or black) type and a red accent:

| Graphic | What's on it |
|---|---|
| **Stats** | Sends, top grade and session time, plus a mini grade pyramid, in Strava's overlay format |
| **Grade pyramid** | Sends per V grade, hardest on top |
| **Climb list** | Every climb with its grade, angle, and tries/flash/project |
| **Hardest send** | The climb lit up on the TB2 Mirror or Spray 12×12 layout. The holds are colorless on a transparent background, with the climb's holds ringed in their start/hand/finish/foot colors. Shows name, grade, angle and tries. You can pick any climb from the session. |

Each graphic comes in two formats: **Sticker**, cropped tight to the content (copy it and paste it into an Instagram story), and **Story**, a full 1080×1920 frame. In the app you can preview each one on a sky, dark, light or your own photo backdrop.

Board angle never changes how the board is drawn. Angles are listed per climb.

## Setup

You need Python 3.10 or newer.

```bash
git clone https://github.com/aaronlberney/climbing-strava.git
cd climbing-strava
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Download the TB2 board data and sign in. This runs once and takes a few minutes:

```bash
python -m juglogger setup
```

This asks for your Tension username and password, then:

1. Signs you in and keeps only the session token, stored in `data/juglogger.sqlite3`. Your password is never saved.
2. Downloads the Tension board database to `data/tension.sqlite3`. This is the public climbs, grades and holds data, taken from the Tension Android app via BoardLib.

The board images ship with the app (`juglogger/assets/`), so nothing else is downloaded.

You can set the `TENSION_PASSWORD` environment variable to skip the password prompt.

Then check that everything lines up with your account:

```bash
python -m juglogger doctor
```

`doctor` also lines up the board database with the layout images and saves `data/calibration-mirror.png` and `data/calibration-spray.png`. In those, every red ring should sit on a hold. If something looks off, paste the output (and those images) into the chat.

## Run it

```bash
python -m juglogger serve
```

This opens http://127.0.0.1:8000. To click around with a fake board and fake climbs, without signing in, run:

```bash
python -m juglogger serve --demo
```

In demo mode a running session gains a climb every 45 seconds.

## How a session works

1. Press **Start**. JugLogger records the start time.
2. Climb and log in the **Tension app** as usual, both sends and attempts. While the timer runs, JugLogger checks your Tension logbook about every 20 seconds and lists the climbs as they appear.
3. Press **Stop**. The session covers every climb logged between start and stop, plus a 5 minute grace period for the last climb you log after stopping.
4. Share the graphics. If a climb is missing because Tension hadn't synced it yet, use **Refresh from Tension**. If the window was wrong, adjust the start and end times under **Session settings**.

To try the graphics on climbs you've already logged, use **Log a past session** on the home screen and enter the start and end times.

### How the numbers are counted

- **Climb**: one climb at one angle, mirrored or not. Repeats in the same session are merged.
- **Tries**: the attempts on your logged send plus any attempts logged before it in the session. Attempts logged after the send don't count toward tries-to-send.
- **Flash**: sent on the first try.
- **Grade**: the grade you logged with your send. Unsent climbs use the board's grade at that angle. To always use the board's consensus grade, start the server with `JUGLOGGER_GRADES=displayed`.
- **Hardest send**: the highest grade sent. Ties go to the one with fewer tries.

## Project layout

```
juglogger/
  __main__.py        CLI: setup, doctor, serve
  server.py          FastAPI app and JSON API
  tension.py         Tension API calls (through BoardLib)
  boarddb.py         Reads the TB2 board database: climbs, grades, holds, images
  store.py           JugLogger's SQLite: account, sessions, logbook copy
  summary.py         Session window → summary (sends, tries, pyramid, hardest)
  graphics/
    render.py        The overlay graphics (Pillow)
    board.py         Lit-up board rendering
    calibrate.py     Lines up database hole positions with the layout images
  assets/            Colorless, transparent TB2 Mirror/Spray 12x12 hold images
                     (+ hold centers), built from assets/source/ by
                     scripts/build_layout_assets.py
  demo.py            Fake TB2 board and logbook for demo mode and tests
  web/               Single-page UI (plain HTML/CSS/JS, no build step)
  fonts/             Outfit and JetBrains Mono (SIL Open Font License)
tests/
data/                Your local data (gitignored)
```

Run the tests with `pytest`.

## Ideas / roadmap

- More boards: Kilter (new API), Moonboard, other Aurora boards
- Running on a phone at the gym (hosted, or a PWA)
- Session photos and a feed, followers, kudos
- Progress over time: grade history, volume, "first V8" milestones
- More graphics: session heatmap of holds used, weekly recap, best-of-month
