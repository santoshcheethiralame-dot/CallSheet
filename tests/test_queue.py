import sqlite3

from callsheet.domain import Shot
from callsheet.queue import enqueue_manifest, frames_done, init_db, mark_done, snapshot

SHOTS = [
    Shot("SH001", "a.blend", 16, [1, 2]),
    Shot("SH002", "b.blend", 64, [1]),
]


def _conn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def test_enqueue_creates_one_job_per_frame():
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    jobs = snapshot(conn)
    assert len(jobs) == 3
    assert all(job.state == "pending" for job in jobs)


def test_positions_preserve_manifest_order():
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    jobs = snapshot(conn)
    assert [job.position for job in jobs] == [0, 1, 2]
    assert [job.shot_id for job in jobs] == ["SH001", "SH001", "SH002"]


def test_frames_done_counts_only_completed_jobs():
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    assert frames_done(conn) == {}

    mark_done(conn, "SH001", 1)

    assert frames_done(conn) == {"SH001": 1}


def test_marking_done_twice_is_idempotent():
    """A retried worker must not inflate progress."""
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    mark_done(conn, "SH001", 1)
    mark_done(conn, "SH001", 1)
    assert frames_done(conn) == {"SH001": 1}


def test_enqueueing_twice_does_not_duplicate_jobs():
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    enqueue_manifest(conn, SHOTS)
    assert len(snapshot(conn)) == 3


def test_a_downgraded_shot_is_enqueued_at_its_own_quality():
    conn = _conn()
    enqueue_manifest(conn, [Shot("SH003", "c.blend", 256, [1], quality="proxy")])
    assert [job.quality for job in snapshot(conn)] == ["proxy"]


def test_reenqueueing_does_not_resurrect_completed_work():
    """The manifest is re-read every round; done frames must stay done."""
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    mark_done(conn, "SH001", 1)
    enqueue_manifest(conn, SHOTS)
    assert frames_done(conn) == {"SH001": 1}
