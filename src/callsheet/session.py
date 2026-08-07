"""The state a running night accumulates. No clock, no I/O, no network.

`board.py` is pure and sees exactly one round; `round.py` produces one round and
then forgets it. Something has to hold what carries *across* rounds — how many
amendments have been issued and what the feed has already said — and that is
this file. It takes `RoundResult`s in and hands `BoardState`s out.

Everything here exists to answer one question honestly: **has anything changed?**
The timer fires far more often than the farm changes, so a session that reported
every tick would produce a call sheet reissued every few seconds and a feed that
scrolls without saying anything. Both would look alive and mean nothing.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from callsheet import queue
from callsheet.board import BoardEvent, BoardState, build_board, revision_stock
from callsheet.decide import MODEL_UNAVAILABLE, Action, Decision
from callsheet.domain import Review, Shot
from callsheet.forecast import Forecast, misses
from callsheet.guard import surviving
from callsheet.round import RoundResult

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "callsheet.db"
"""The night's queue, on disk rather than in memory: a server restarted at 2am
must not forget what has already rendered."""

MAX_EVENTS = 200
"""The feed shows the most recent handful; this is the tail the server keeps.
A night runs for hours on a timer, so an uncapped list is a slow leak that also
grows every SSE frame on the wire."""

__all__ = ["MODEL_UNAVAILABLE", "Session", "amendment", "open_night", "situation"]
"""`MODEL_UNAVAILABLE` is re-exported rather than defined here: the feed and the
banner have to say the same thing, and the banner's copy is chosen in `decide`,
which is the only module that knows what went wrong with the model."""

SHORTFALL_BUCKET_S = 10
"""How much a shortfall must move before it counts as a different situation.

The free tier allows 20 model calls per day per model. A round fires every 30s
and a fresh night misses on every one of them, so asking on every miss spends
the day in ten minutes. The fix is to ask only when the *question* is new, and
that requires deciding when two questions are the same.

Bucketing, not equality, because the raw shortfall never repeats exactly: the
forecast rounds up to the whole second, telemetry means drift with every frame,
and a shot that is 63s short this round is 62s short the next while being the
identical production problem with the identical answer. Ten seconds is set
against what a change would have to be worth: a frame is 8-60s here, so one
frame finishing early or a render slowing down moves a shortfall clear across a
bucket, while per-frame jitter and rounding do not. Coarser and a real shift in
the plan could hide inside a bucket; finer and the drift alone re-asks.

The one artefact is the boundary: a shortfall crossing 19s -> 20s re-asks over
one second of movement. That is the cheap direction to fail in - it costs one
call, where the other direction would reuse a stale plan.
"""


def situation(forecasts: Sequence[Forecast], review: Review) -> frozenset:
    """What the model would be asked about, as a comparable key.

    Which required shots are going to be late, and by roughly how much. Nothing
    else: not the wording, not the clock, not the shots that are on time, and
    not the absolute finish times — `run_rounds` re-bases the deadline on `now`
    every round, so an ETA moves 30s per round while the farm stands still. The
    *shortfall* is what holds steady, which is the whole reason it is the thing
    keyed on.

    An empty key means no shot is missing, which is not a situation anyone needs
    judged; `Session.reuse` treats it as nothing to reuse rather than as a
    situation that happens to match.
    """
    return frozenset(
        (forecast.shot_id,
         (forecast.finishes_at_epoch_s - review.deadline_epoch_s) // SHORTFALL_BUCKET_S)
        for forecast in misses(list(forecasts))
    )


def amendment(decision: Decision | None, applied: Sequence[Action]) -> tuple | None:
    """What makes this call sheet different from the last one.

    The actions **the guard allowed** — what the crew is actually being told to
    do — and nothing else. Proposed-but-rejected actions are excluded because
    they were never carried out, and reissuing the paper for something the code
    refused would announce an amendment that did not happen.

    No prose. Not `Action.reason`, and not `decision.summary` either, which is
    the harder call and was settled by watching it run: with the farm unchanged
    and the same prompt, two consecutive rounds returned the identical plan
    (preempt SH002) worded differently — "to allow required shot SH003 to
    finish", then "to clear the queue ahead of SH003" — and keying on the
    summary reissued the call sheet for it. That is the exact failure the
    revision exists to avoid: advancing on the timer rather than on a decision,
    which makes the stock colour a clock. A production reissues coloured paper
    when the instructions change, not when someone rephrases a note; the newest
    summary still prints on the paper either way, it just does not claim to be
    a new revision.
    """
    if decision is None:
        return None
    return tuple((action.shot_id, action.action) for action in applied)


def open_night(shots: Sequence[Shot], out_dir: Path,
               path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the job queue for tonight and make it agree with the disk.

    Three steps, in this order and no other. The schema, then the manifest —
    `enqueue_manifest` never resurrects a finished row, so re-running it is
    free. Then the reconciliation: every frame whose PNG is already sitting in
    `out/` is marked done, because a queue that starts a fresh boot claiming
    nine frames of work when nine frames exist would send the forecaster off to
    re-render a finished night.

    Reconciling *from* disk rather than trusting it thereafter is the point.
    Disk seeds the queue once; from then on the queue is what anyone asks.
    """
    conn = sqlite3.connect(path, check_same_thread=False)
    queue.init_db(conn)
    queue.enqueue_manifest(conn, list(shots))
    reconcile_with_disk(conn, shots, out_dir)
    return conn


def reconcile_with_disk(conn: sqlite3.Connection, shots: Sequence[Shot],
                        out_dir: Path) -> list[tuple[str, int]]:
    """Mark done every pending frame whose PNG has appeared. Returns what landed.

    Called once at boot and again every round. Once-at-boot was not enough: a
    frame finishing mid-night stayed invisible until a restart, so the board
    showed a farm that never progressed while Blender worked behind it. A board
    that cannot show work arriving is not a board.

    It syncs **both** ways. An earlier version only ever marked frames done, on
    the rule that disk seeds the queue once and the queue answers thereafter.
    Deleting the rendered frames proved that wrong: the queue went on claiming a
    shot was complete, the board pointed a thumbnail at a file that no longer
    existed, and the row rendered as a broken image. On whether a frame exists,
    the disk is the authority.

    A `preempted` row is left alone either way — that state is a production
    decision, not an observation, and a missing file must not overturn it.
    """
    out = Path(out_dir)
    landed = []

    for job in queue.snapshot(conn):
        if job.state == "preempted":
            continue
        exists = (out / f"{job.shot_id}_{job.frame:04d}.png").exists()
        if exists and job.state != "done":
            queue.mark_done(conn, job.shot_id, job.frame)
            landed.append((job.shot_id, job.frame))
        elif not exists and job.state == "done":
            queue.mark_undone(conn, job.shot_id, job.frame)

    return landed


class Session:
    """One running night. Mutable on purpose; the board it hands out is not."""

    def __init__(self, max_events: int = MAX_EVENTS,
                 conn: sqlite3.Connection | None = None) -> None:
        self.max_events = max_events
        self.conn = conn
        """The night's job queue, or `None` when there is no live round behind
        this session — the board tests and the no-credentials server path."""

        self.revision = 0
        self.events: list[BoardEvent] = []
        self.board: BoardState | None = None
        """`None` until the first round lands, so the server can tell "no round
        has run yet" from "a round ran and found nothing wrong"."""

        self._issued: tuple | None = None
        self._degraded: str | None = None
        self._missing: frozenset[str] = frozenset()

        self._situation: frozenset = frozenset()
        self._decision: Decision | None = None
        """The last situation judged, and what was judged about it. Together
        they are the standing answer `reuse` hands back."""

    def reuse(self, forecasts: Sequence[Forecast], review: Review) -> Decision | None:
        """The decision already made about this situation, if it is this one.

        Handed to `run_round` so the model is asked only when the question has
        changed. This is the same judgement `amendment` makes — "is this really
        different?" — moved to where it can still save something. `amendment`
        makes it *after* the call, which stops the paper being reissued but not
        the quota being spent; this makes it before. The two now stack: this one
        keeps an unchanged night from asking, and `amendment` still catches the
        case where the situation genuinely changed and the model came back with
        the same plan anyway.

        A situation whose last call *failed* is not reusable — there is nothing
        to reuse — so a degraded round retries next tick. That is deliberate:
        the quota rolls over at Pacific midnight and a night running through it
        should recover by itself. The retries cost nothing while it is spent,
        because a request refused for quota is refused before it is counted.
        """
        key = situation(forecasts, review)
        if key and key == self._situation and self._decision is not None:
            return self._decision
        return None

    def progress(self) -> dict[str, int]:
        """Frames completed per shot, from the queue and nowhere else.

        The caller hands this same dict to `run_round` and to `record`, which is
        the whole fix: the forecast and the cards cannot disagree about how much
        of a shot is finished when they are reading one number.
        """
        return queue.frames_done(self.conn) if self.conn is not None else {}

    def catch_up(self, shots: Sequence[Shot], out_dir: Path) -> list[tuple[str, int]]:
        """Take in any frame that has landed since the last round.

        A no-op with no queue, so a session running without one degrades to
        showing no progress rather than raising.
        """
        if self.conn is None:
            return []
        return reconcile_with_disk(self.conn, shots, out_dir)

    def say(self, text: str, at_epoch_s: int) -> None:
        self.events.append(BoardEvent(at_epoch_s, text))
        del self.events[: max(0, len(self.events) - self.max_events)]

    def record(
        self,
        result: RoundResult,
        shots: Sequence[Shot],
        review: Review,
        *,
        frames_done: Mapping[str, int] | None = None,
        now_epoch_s: int,
    ) -> BoardState:
        """Fold one round into the night and return the board as it now stands.

        `frames_done` comes from the driver rather than the round because the
        driver holds the queue, and it must be the *same* dict the driver gave
        `run_round`. Passing the queue's answer here and letting the forecast
        use another is precisely the bug this argument exists to prevent: a card
        reading 3/3 beside a call sheet saying the shot is 18s short.
        """
        applied = (surviving(result.decision.actions, result.guard_rejections)
                   if result.decision is not None else [])

        self._announce_misses(result, review, now_epoch_s)
        self._announce_degrade(result, now_epoch_s)
        self._issue(result.decision, applied, now_epoch_s)
        self._remember(result, review)

        self.board = build_board(
            shots, review, result.forecasts, result.decision, applied,
            result.guard_rejections, result.residuals,
            revision=self.revision,
            frames_done=frames_done,
            events=self.events,
            degraded_reason=result.degraded_reason,
            now_epoch_s=now_epoch_s,
        )
        return self.board

    def _announce_misses(self, result: RoundResult, review: Review,
                         now_epoch_s: int) -> None:
        """Name a shot the first time it is going to be late, and then stop.

        Repeating it every tick would bury the moment it happened under forty
        copies of itself, and the feed exists to record moments.
        """
        behind = [forecast.shot_id for forecast in misses(result.forecasts)]
        when = time.strftime("%H:%M", time.localtime(review.deadline_epoch_s))
        for shot_id in behind:
            if shot_id not in self._missing:
                self.say(f"{shot_id} will not make the {when} review", now_epoch_s)
        self._missing = frozenset(behind)

    def _announce_degrade(self, result: RoundResult, now_epoch_s: int) -> None:
        if result.degraded_reason and result.degraded_reason != self._degraded:
            self.say(MODEL_UNAVAILABLE, now_epoch_s)
        self._degraded = result.degraded_reason

    def _remember(self, result: RoundResult, review: Review) -> None:
        """Note what the round was asked and what came back, for `reuse`.

        Recorded here rather than in `reuse` so the pairing cannot drift: the
        key is taken from the forecasts the decision was actually made against,
        not from the ones that were current when the question was asked.
        """
        self._situation = situation(result.forecasts, review)
        self._decision = result.decision

    def _issue(self, decision: Decision | None, applied: Sequence[Action],
               now_epoch_s: int) -> None:
        """Advance the revision only when the call sheet actually changed."""
        current = amendment(decision, applied)
        if current is None or current == self._issued:
            return
        self._issued = current
        self.revision += 1
        self.say(f"Revision {self.revision} issued - {revision_stock(self.revision)}",
                 now_epoch_s)
