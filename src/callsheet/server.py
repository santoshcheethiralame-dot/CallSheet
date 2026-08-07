"""The production surface: one page, the board state, and the real frames.

Every route here is a view. Nothing in this file decides anything about the
schedule — it reads what the engine produced and hands it to the page.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from callsheet.board import BoardState, build_board
from callsheet.domain import Review, load_review, load_shots

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "out"
PAGE = ROOT / "web" / "index.html"
MANIFEST = ROOT / "scenes" / "manifest.json"
REVIEW = ROOT / "review.json"

PLACEHOLDER = "<!doctype html><title>CALLSHEET</title><h1>CALLSHEET</h1>"
"""Served until `web/index.html` exists. The name is the whole content on
purpose: a placeholder that looked like a product would hide the fact that the
page is not built yet."""

TICK_S = 1.0

app = FastAPI(title="CALLSHEET")


def current_board() -> BoardState:
    """The board as it stands right now.

    Until a live round drives it, this reports only what is on disk: the shot
    manifest, the review, and the frames that have actually been rendered. No
    forecasts, because a forecast needs telemetry and this must serve with no
    credentials present. Every field is therefore either measured or absent —
    the page never receives an invented number.
    """
    shots = load_shots(str(MANIFEST)) if MANIFEST.exists() else []
    review = (load_review(str(REVIEW)) if REVIEW.exists()
              else Review("No review scheduled", 0, []))
    frames_done = {
        shot.id: len(list(OUT_DIR.glob(f"{shot.id}_*.png"))) for shot in shots
    } if OUT_DIR.is_dir() else {}

    return build_board(shots, review, [], now_epoch_s=review.deadline_epoch_s,
                       frames_done=frames_done)


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
    root = OUT_DIR.resolve()
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
