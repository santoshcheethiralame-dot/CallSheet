import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from callsheet import server
from callsheet.config import ConfigError
from callsheet.decide import Action, Decision
from callsheet.domain import Review, Shot
from callsheet.round import RoundResult
from callsheet.server import app
from callsheet.session import Session

client = TestClient(app)
"""Constructed outside a `with` block on purpose: that is what keeps the app's
lifespan — and therefore the live round — from starting for these tests."""


def test_the_page_is_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "CALLSHEET" in response.text


def test_state_endpoint_returns_the_board():
    response = client.get("/api/state")
    assert response.status_code == 200
    body = response.json()
    assert "cards" in body
    assert "revision" in body


def test_a_frame_outside_the_output_directory_is_refused():
    """The frames route takes a filename from the URL. It must not walk out."""
    response = client.get("/frames/..%2F..%2F.env")
    assert response.status_code in (400, 404)


def test_a_missing_frame_is_a_404_not_a_crash():
    assert client.get("/frames/SH999_9999.png").status_code == 404


def test_a_rendered_frame_is_served(tmp_path, monkeypatch):
    """Without this the guard could pass every test above by refusing everything."""
    (tmp_path / "SH001_0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(server, "OUT_DIR", tmp_path)

    response = client.get("/frames/SH001_0001.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_a_backslash_traversal_is_refused(tmp_path, monkeypatch):
    """The one that actually reaches the handler on Windows.

    A percent-encoded forward slash is decoded before routing, so `..%2F..%2F`
    never matches the single path segment and dies in the router. A backslash
    does match, and Windows resolves it as a separator, so this encoding is what
    the explicit check exists for.
    """
    monkeypatch.setattr(server, "OUT_DIR", tmp_path)
    assert client.get("/frames/..%5C..%5C.env").status_code in (400, 404)


def test_an_absolute_path_is_refused(tmp_path, monkeypatch):
    """`Path(out) / "C:/..."` discards the left-hand side entirely."""
    monkeypatch.setattr(server, "OUT_DIR", tmp_path)
    assert client.get("/frames/C:%5CWindows%5Cwin.ini").status_code in (400, 404)


def test_the_server_starts_and_serves_with_no_credentials(monkeypatch):
    """The path a judge cloning the repo is on. No .env, no Grafana, no key —
    and the surface must still come up rather than 500 on the first request.

    Forced rather than observed: this machine has a populated .env, so a test
    that merely started the app would prove the opposite of what it claims.
    """
    def no_credentials() -> None:
        raise ConfigError("Missing required environment variables: GEMINI_API_KEY")

    monkeypatch.setattr(server, "load_config", no_credentials)
    monkeypatch.setattr(server, "_session", None)

    with TestClient(app) as bare:
        response = bare.get("/api/state")

    assert response.status_code == 200
    assert response.json()["revision"] == 0
    assert server._session is None, "no credentials must mean no live round"


def test_the_board_comes_from_the_live_round_once_one_has_run(monkeypatch):
    session = Session()
    session.record(
        RoundResult([], Decision("Striking SH002", [Action("SH002", "preempt", "cut")]),
                    True),
        [Shot("SH002", "b.blend", 64, [1, 2, 3])],
        Review("Director review", 1_000_030, ["SH001"]),
        frames_done={}, now_epoch_s=1_000_000,
    )
    monkeypatch.setattr(server, "_session", session)

    body = client.get("/api/state").json()

    assert body["revision"] == 1
    assert body["stock"] == "blue"
    assert body["summary"] == "Striking SH002"


def test_a_degraded_round_reaches_the_page(monkeypatch):
    """The page reads `degraded_reason` to raise its banner. If the field never
    arrives the board reports a model outage as a quiet, healthy night."""
    session = Session()
    session.record(RoundResult([], None, False, degraded_reason="503 UNAVAILABLE"),
                   [], Review("Director review", 1_000_030, []),
                   frames_done={}, now_epoch_s=1_000_000)
    monkeypatch.setattr(server, "_session", session)

    assert client.get("/api/state").json()["degraded_reason"] == "503 UNAVAILABLE"


def test_the_stream_event_carries_the_whole_board():
    """Tested at the wire format, not over HTTP: subscribing a test client to an
    endless stream hangs the suite rather than failing it."""
    event = server.state_event()

    assert event.startswith("event: state\ndata: ")
    assert event.endswith("\n\n")
    payload = json.loads(event.split("data: ", 1)[1])
    assert "cards" in payload
    assert "revision" in payload


@pytest.mark.asyncio
async def test_the_timer_hands_the_round_the_session_that_remembers():
    """The wiring, pinned. Everything that keeps this system inside the free
    tier lives behind one keyword argument: the round asks whoever it was given
    whether this situation has already been judged, and in production that has
    to be the session holding the night's memory. Dropped, the round silently
    goes back to asking the model on every miss and the day's 20 calls are gone
    in ten minutes — with every test but this one still green.
    """
    session = Session()
    seen: dict = {}

    async def capture(*_args, **kwargs):
        seen.update(kwargs)
        # The one way out of an endless timer. `run_rounds` catches `Exception`
        # and keeps its next appointment; `CancelledError` is not one.
        raise asyncio.CancelledError

    with patch("callsheet.server.run_round", capture), \
         pytest.raises(asyncio.CancelledError):
        # The config is only ever handed onward to the round, which is patched.
        await server.run_rounds(None, session, [],
                                Review("Director review", 1_000_030, []))

    assert seen["reuse"] == session.reuse
