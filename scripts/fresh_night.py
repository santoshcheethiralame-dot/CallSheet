"""Clear the night so the board has something to demonstrate.

Once progress became truthful the board went quiet, and correctly so: nine
frames exist, every shot is in the can, and a finished night has nothing to
show. So this puts the stage back to empty — the frames go, every job returns
to `pending`, and the next session opens on white paper. A night that starts
empty and goes wrong is better footage than a night that is already over.

Run before recording the demo, with the server stopped:

    python scripts/fresh_night.py

It deletes real files, so it is deliberately narrow. It touches `out/` and the
job queue and nothing else — never `scenes/`, never `.env` — it prints every
file before removing it, and it refuses outright if `out/` holds anything that
is not a rendered frame rather than guessing what a stranger's file is for.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from callsheet.domain import load_shots
from callsheet.queue import enqueue_manifest, init_db
from callsheet.session import DB_PATH, ROOT

FRAME_GLOB = "SH*_*.png"
"""What this script is allowed to delete. Anything else in `out/` stops it."""

OUT_DIR = ROOT / "out"
MANIFEST = ROOT / "scenes" / "manifest.json"


def strays(out_dir: Path) -> list[Path]:
    """Everything in `out/` that is not a rendered frame.

    A directory counts as a stray too. The point is not to classify what is
    there — it is that anything unrecognised means this script's idea of the
    output directory and reality have diverged, and a script that deletes files
    must stop when that happens rather than proceed on a guess.
    """
    frames = set(out_dir.glob(FRAME_GLOB))
    return sorted(path for path in out_dir.iterdir() if path not in frames)


def reset_queue(conn: sqlite3.Connection) -> int:
    """Put every finished job back to `pending`. Returns how many moved."""
    cursor = conn.execute("UPDATE jobs SET state = 'pending' WHERE state != 'pending'")
    conn.commit()
    return cursor.rowcount


def main() -> int:
    frames: list[Path] = []
    if OUT_DIR.is_dir():
        stray = strays(OUT_DIR)
        if stray:
            print(f"REFUSING: {OUT_DIR} holds {len(stray)} file(s) that are not "
                  f"rendered frames:")
            for path in stray:
                print(f"  {path.name}")
            print("Move them elsewhere first. This script deletes files and will "
                  "not guess what yours are for.")
            return 1
        frames = sorted(OUT_DIR.glob(FRAME_GLOB))

    print(f"Deleting {len(frames)} rendered frame(s) from {OUT_DIR}:")
    for path in frames:
        print(f"  {path.name}")
    for path in frames:
        path.unlink()

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    # Seeded as well as reset: running this before the server has ever opened
    # the queue should still leave a complete queue of pending work behind.
    enqueue_manifest(conn, load_shots(str(MANIFEST)))
    moved = reset_queue(conn)
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    print(f"Queue: {moved} job(s) returned to pending, {total} pending in total.")
    print("Revision: 0 - a session starts on white paper, so start the server "
          "after this, not before.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
