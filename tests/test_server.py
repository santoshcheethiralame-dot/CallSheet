import json

from fastapi.testclient import TestClient

from callsheet import server
from callsheet.server import app

client = TestClient(app)


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


def test_the_stream_event_carries_the_whole_board():
    """Tested at the wire format, not over HTTP: subscribing a test client to an
    endless stream hangs the suite rather than failing it."""
    event = server.state_event()

    assert event.startswith("event: state\ndata: ")
    assert event.endswith("\n\n")
    payload = json.loads(event.split("data: ", 1)[1])
    assert "cards" in payload
    assert "revision" in payload
