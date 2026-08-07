"""The only script in the repo that deletes files, so its guard is tested.

Loaded by path rather than imported: `scripts/` is not a package, and making it
one to test eighty lines of demo plumbing would be the tail wagging the dog.
"""

import importlib.util
import json
import sqlite3
from pathlib import Path

from callsheet.domain import Shot
from callsheet.queue import enqueue_manifest, frames_done, init_db, mark_done

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fresh_night.py"

_spec = importlib.util.spec_from_file_location("fresh_night", SCRIPT)
fresh_night = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fresh_night)

SHOTS = [Shot("SH001", "a.blend", 16, [1, 2])]


def _stage(tmp_path, monkeypatch, *, frames=("SH001_0001.png", "SH001_0002.png")):
    """An out/ directory, a manifest and a queue, all inside tmp_path.

    Everything the script touches is redirected here, so a test that got the
    guard wrong destroys a temporary directory rather than the repo.
    """
    out = tmp_path / "out"
    out.mkdir()
    for name in frames:
        (out / name).write_bytes(b"\x89PNG")

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([
        {"shot": s.id, "scene": s.scene, "samples": s.samples, "frames": s.frames}
        for s in SHOTS
    ]), encoding="utf-8")

    db = tmp_path / "callsheet.db"
    monkeypatch.setattr(fresh_night, "OUT_DIR", out)
    monkeypatch.setattr(fresh_night, "MANIFEST", manifest)
    monkeypatch.setattr(fresh_night, "DB_PATH", db)
    return out, db


def test_it_clears_the_frames_and_returns_every_job_to_pending(tmp_path, monkeypatch):
    out, db = _stage(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    init_db(conn)
    enqueue_manifest(conn, SHOTS)
    mark_done(conn, "SH001", 1)
    conn.close()

    assert fresh_night.main() == 0

    assert list(out.iterdir()) == []
    conn = sqlite3.connect(db)
    assert frames_done(conn) == {}, "a fresh night starts on an empty stage"
    assert len(conn.execute("SELECT 1 FROM jobs").fetchall()) == 2, \
        "the work is pending again, not deleted"


def test_it_says_what_it_will_delete_and_how_many(tmp_path, monkeypatch, capsys):
    """It removes real files, so the operator reads the list before it happens."""
    _stage(tmp_path, monkeypatch)

    fresh_night.main()

    printed = capsys.readouterr().out
    assert "Deleting 2 rendered frame(s)" in printed
    assert "SH001_0001.png" in printed
    assert "SH001_0002.png" in printed


def test_it_refuses_when_out_holds_anything_that_is_not_a_frame(tmp_path, monkeypatch):
    """A stranger's file in out/ means this script's idea of the directory and
    reality have diverged. It stops rather than deleting on a guess."""
    out, db = _stage(tmp_path, monkeypatch)
    (out / "notes.txt").write_text("keep me", encoding="utf-8")

    assert fresh_night.main() == 1

    assert (out / "notes.txt").exists()
    assert (out / "SH001_0001.png").exists(), "it refuses before deleting anything"
    assert not db.exists(), "and before touching the queue"


def test_a_directory_in_out_is_a_stray_too(tmp_path, monkeypatch):
    out, _ = _stage(tmp_path, monkeypatch)
    (out / "archive").mkdir()

    assert fresh_night.main() == 1


def test_it_seeds_a_queue_that_has_never_been_opened(tmp_path, monkeypatch):
    """Running this before the server has ever run must still leave a complete
    queue of pending work behind, not an empty database."""
    _, db = _stage(tmp_path, monkeypatch, frames=())

    assert fresh_night.main() == 0

    conn = sqlite3.connect(db)
    assert len(conn.execute("SELECT 1 FROM jobs").fetchall()) == 2
