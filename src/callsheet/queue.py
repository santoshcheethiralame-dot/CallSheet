"""The job queue. Source of truth for what work remains.

One row per frame, because a frame is the unit a worker actually claims and the
unit a forecast counts. `position` is the render order: the farm is a single
serial queue, so position is what makes "sacrificing a shot behind the at-risk
shot recovers nothing" a checkable fact rather than a claim in a prompt.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from callsheet.domain import Shot

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    shot_id  TEXT NOT NULL,
    frame    INTEGER NOT NULL,
    quality  TEXT NOT NULL DEFAULT 'final',
    state    TEXT NOT NULL DEFAULT 'pending',
    position INTEGER NOT NULL,
    PRIMARY KEY (shot_id, frame)
);
"""


@dataclass(frozen=True)
class Job:
    shot_id: str
    frame: int
    quality: str
    state: str
    position: int


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def enqueue_manifest(conn: sqlite3.Connection, shots: list[Shot]) -> None:
    """Idempotent: re-running against the same manifest changes nothing.

    `INSERT OR IGNORE` rather than upsert, deliberately. The manifest is re-read
    every round, and a row that already exists carries state a re-read must not
    overwrite: a frame marked `done` would otherwise be resurrected as `pending`
    and rendered a second time.
    """
    position = 0
    for shot in shots:
        for frame in shot.frames:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (shot_id, frame, quality, state, position)"
                " VALUES (?, ?, ?, 'pending', ?)",
                (shot.id, frame, shot.quality, position),
            )
            position += 1
    conn.commit()


def snapshot(conn: sqlite3.Connection) -> list[Job]:
    """Every job, in render order. The pure modules take this, never a cursor."""
    rows = conn.execute(
        "SELECT shot_id, frame, quality, state, position FROM jobs ORDER BY position"
    ).fetchall()
    return [Job(*row) for row in rows]


def mark_done(conn: sqlite3.Connection, shot_id: str, frame: int) -> None:
    conn.execute(
        "UPDATE jobs SET state = 'done' WHERE shot_id = ? AND frame = ?", (shot_id, frame)
    )
    conn.commit()


def frames_done(conn: sqlite3.Connection) -> dict[str, int]:
    """Completed frames per shot, in the shape `FarmState.frames_done` expects."""
    rows = conn.execute(
        "SELECT shot_id, COUNT(*) FROM jobs WHERE state = 'done' GROUP BY shot_id"
    ).fetchall()
    return {shot_id: count for shot_id, count in rows}
