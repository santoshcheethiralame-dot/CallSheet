"""The production surface: one page, the board state, and the real frames.

Every route here is a view. Nothing in this file decides anything about the
schedule — it reads what the engine produced and hands it to the page. The one
thing it owns is the *timer*: a background task that runs a scheduling round,
folds it into the session, and lets the routes serve the result.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from callsheet.board import BoardState, build_board
from callsheet.config import Config
from callsheet.domain import Review, Shot, load_review, load_shots
from callsheet.round import RoundResult, run_round
from callsheet.session import Session, open_night

log = logging.getLogger(__name__)

def _find_root(counted: Path | None = None) -> Path:
    """Where the page, the manifest, the review and the frames live.

    Counting directories up from `__file__` is a guess about how the package was
    installed, and it is wrong as soon as it is installed at all: from
    `site-packages/callsheet/server.py`, `parents[2]` lands in the Python install
    tree. Every asset path then points at nothing, nothing raises, and the server
    serves its placeholder with a 200 while reporting itself healthy. That is
    what the first Render deploy did, and the reason it took a while to see is
    that a confident lie looks exactly like success from outside.

    So look for a file only the root has, rather than counting. `CALLSHEET_ROOT`
    wins when set; the container's working directory is the next candidate,
    because a container that copies the tree in also chdirs into it.
    """
    override = os.environ.get("CALLSHEET_ROOT")
    if override:
        return Path(override)

    if counted is None:
        counted = Path(__file__).resolve().parents[2]
    for candidate in (counted, Path.cwd()):
        if (candidate / "web" / "index.html").is_file():
            return candidate
    return counted


ROOT = _find_root()

OUT_DIR = ROOT / "out"
BAKED_DIR = ROOT / "demo" / "frames"
"""Real frames from a real render, committed so a clone or a container has
something to show. The hosted board does not run Blender — rendering is local
work that produces telemetry, and the hosted surface is the agent reading that
telemetry back."""


def frames_dir() -> Path:
    """Where frames are served from: a live render if there is one, else the
    baked set. One directory at a time, never a search path — the traversal
    guard is only sound because there is exactly one root to be inside of."""
    if OUT_DIR.is_dir() and any(OUT_DIR.glob("*.png")):
        return OUT_DIR
    return BAKED_DIR


PAGE = ROOT / "web" / "index.html"
MANIFEST = ROOT / "scenes" / "manifest.json"
REVIEW = ROOT / "review.json"

PLACEHOLDER = "<!doctype html><title>CALLSHEET</title><h1>CALLSHEET</h1>"
"""Served until `web/index.html` exists. The name is the whole content on
purpose: a placeholder that looked like a product would hide the fact that the
page is not built yet."""

TICK_S = 1.0

ROUND_INTERVAL_S = 30.0
"""One scheduling round every 30s — the timer of record from §5.3."""

DEADLINE_S = 30
"""How far out the next review sits, applied to the clock each round.

`review.json` carries `deadline_epoch_s = 0` as a sentinel because a committed
absolute deadline is in the past by the next morning. `demo_round.py` applies
the clock the same way; doing it per round rather than once at startup keeps the
board showing the next review rather than an ever-deepening overdue."""

_session: Session | None = None
"""The running night, or `None` when no live round is driving the board."""


def load_config() -> Config:
    """Credentials for the live round. Raises when they are absent.

    A named function so the no-credentials path is testable without unsetting
    environment variables underneath a process that may have a `.env`."""
    load_dotenv()
    return Config.from_env(os.environ)


def frames_on_disk(shots: list[Shot]) -> dict[str, int]:
    """What the farm has actually written, counted straight off the filesystem.

    Only for the board served when no round is running — no credentials, no
    session, therefore no queue. Every other path reads `Session.progress()`,
    which is the queue; this one exists so a judge with an empty `.env` still
    gets cards that match the pixels.
    """
    if not OUT_DIR.is_dir():
        return {}
    return {shot.id: len(list(OUT_DIR.glob(f"{shot.id}_*.png"))) for shot in shots}


async def run_rounds(config: Config, session: Session, shots: list[Shot],
                     review: Review) -> None:
    """The timer. It must outlive anything that goes wrong inside it.

    A round that raises — Grafana unreachable, the model out, a bad payload —
    ends this task if it escapes, and the board silently freezes on its last
    good state while continuing to look live. So the failure is caught, recorded
    as a degrade, and the timer keeps its next appointment.
    """
    while True:
        now = int(time.time())
        tonight = dataclasses.replace(review, deadline_epoch_s=now + DEADLINE_S)

        # Pick up any frame that landed since the last round, before reading
        # progress. Reconciling only at boot left the board frozen while Blender
        # worked behind it.
        for shot_id, frame in session.catch_up(shots, OUT_DIR):
            session.say(f"{shot_id} frame {frame} rendered", now)

        # Read once, hand to both. The forecast and the cards disagreeing about
        # how much of a shot is finished was the board's self-contradiction;
        # they cannot disagree about a number they were both handed.
        done = session.progress()
        try:
            # `session.reuse` is what keeps this timer inside the free tier: 20
            # model calls a day, a round every 30s, and a fresh night that
            # misses on every one of them. The session is asked first whether
            # this situation has already been judged.
            result = await run_round(config, shots, tonight, now_epoch_s=now,
                                     frames_done=done, reuse=session.reuse)
        except Exception as error:      # noqa: BLE001 — the loop degrades, never dies
            log.warning("round failed: %s", error)
            result = RoundResult([], None, False, degraded_reason=str(error))

        session.record(result, shots, tonight, frames_done=done, now_epoch_s=now)
        await asyncio.sleep(ROUND_INTERVAL_S)


def start_rounds() -> asyncio.Task | None:
    """Begin the live round, or don't — and serve either way.

    No credentials is a normal way to run this: a judge cloning the repo has no
    Grafana token and must still get the page. So a config failure is not an
    error here, it is the absence of a live round; `current_board` falls back to
    what is on disk and the surface stays up.
    """
    global _session
    try:
        config = load_config()
        shots = load_shots(str(MANIFEST))
        review = load_review(str(REVIEW))
    except Exception as error:          # noqa: BLE001 — serving matters more
        log.info("no live round: %s", error)
        return None

    _session = Session(conn=open_night(shots, OUT_DIR))
    return asyncio.create_task(run_rounds(config, _session, shots, review))


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    task = start_rounds()
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="CALLSHEET", lifespan=lifespan)


def current_board() -> BoardState:
    """The board as it stands right now.

    Once a round has run, this is the session's board — a real forecast, a real
    decision, and the revision the night has reached. Before that it reports
    only what is on disk: the shot manifest, the review, and the frames that
    have actually been rendered. No forecasts on that path, because a forecast
    needs telemetry and this must serve with no credentials present. Every field
    is therefore either measured or absent — the page never receives an invented
    number.
    """
    if _session is not None and _session.board is not None:
        return _session.board

    shots = load_shots(str(MANIFEST)) if MANIFEST.exists() else []
    review = (load_review(str(REVIEW)) if REVIEW.exists()
              else Review("No review scheduled", 0, []))

    return build_board(shots, review, [], now_epoch_s=review.deadline_epoch_s,
                       frames_done=frames_on_disk(shots))


@app.get("/", response_class=HTMLResponse)
def page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8")
                        if PAGE.exists() else PLACEHOLDER)


@app.get("/api/state")
def state() -> BoardState:
    return current_board()


def state_event() -> str:
    """One server-sent event carrying the whole board.

    A named function rather than an inline f-string so the wire format can be
    tested without opening a never-ending HTTP stream — a test client that
    subscribes to an infinite response has to be killed rather than closed.
    """
    payload = json.dumps(jsonable_encoder(current_board()))
    return f"event: state\ndata: {payload}\n\n"


@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    """One `state` event per tick, until the client goes away.

    Server-sent events rather than a socket: the traffic is one-way, it survives
    a proxy, and it costs the page four lines of JavaScript and no dependency.
    """
    async def ticks():
        while not await request.is_disconnected():
            yield state_event()
            await asyncio.sleep(TICK_S)

    return StreamingResponse(ticks(), media_type="text/event-stream")


@app.get("/frames/{name}")
def frame(name: str) -> FileResponse:
    """Serve a rendered frame, and only a rendered frame.

    `name` arrives from the URL and is joined onto a filesystem path, so it is
    hostile until proven otherwise: `..\\..\\.env` survives routing as a single
    segment and Windows treats it as a walk upwards, and an absolute path like
    `C:\\Windows\\win.ini` makes `Path.__truediv__` discard `out/` altogether.
    Both served real files before this check existed. So the resolved path is
    compared against the resolved output directory and anything outside is
    refused. `StaticFiles` would do this too, but it does it somewhere else;
    the one route that takes a filename from a stranger should carry its own
    proof.

    Outside and absent are both 404 on purpose: distinguishing them would turn
    the route into a way to ask what exists on the host.
    """
    root = frames_dir().resolve()
    try:
        candidate = (root / name).resolve()
        inside = candidate.is_relative_to(root) and candidate.is_file()
    except (OSError, ValueError):
        # A name the filesystem cannot even parse (a null byte, an illegal
        # Windows character). Not a frame, and not worth a stack trace.
        inside = False

    if not inside:
        raise HTTPException(status_code=404, detail="no such frame")
    return FileResponse(candidate, media_type="image/png")
