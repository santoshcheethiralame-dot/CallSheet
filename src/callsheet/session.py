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

import time
from collections.abc import Mapping, Sequence

from callsheet.board import BoardEvent, BoardState, build_board, revision_stock
from callsheet.decide import Action, Decision
from callsheet.domain import Review, Shot
from callsheet.forecast import misses
from callsheet.guard import surviving
from callsheet.round import RoundResult

MAX_EVENTS = 200
"""The feed shows the most recent handful; this is the tail the server keeps.
A night runs for hours on a timer, so an uncapped list is a slow leak that also
grows every SSE frame on the wire."""

MODEL_UNAVAILABLE = "Scheduling by priority - the model is unavailable"
"""The degrade, said the way the copy table says it: a working system in a
lesser mode, not a broken one."""


def amendment(decision: Decision | None, applied: Sequence[Action]) -> tuple | None:
    """What makes this call sheet different from the last one.

    The summary and the actions **the guard allowed**, because those two are the
    call sheet: the sentence the coordinator says out loud and the instructions
    the crew is given. Proposed-but-rejected actions are excluded — they were
    never carried out, and reissuing the paper because the model wanted
    something the code refused would announce an amendment that did not happen.

    `Action.reason` is deliberately left out. It is model prose attached to an
    instruction, and it drifts in wording between calls that produce the exact
    same plan; keying on it would issue a fresh revision every timer tick for a
    rephrased note. The summary is prose too and *is* included, because it is
    printed on the paper — if the coordinator says something different, the crew
    is holding a different document.
    """
    if decision is None:
        return None
    return (decision.summary, tuple((a.shot_id, a.action) for a in applied))


class Session:
    """One running night. Mutable on purpose; the board it hands out is not."""

    def __init__(self, max_events: int = MAX_EVENTS) -> None:
        self.max_events = max_events
        self.revision = 0
        self.events: list[BoardEvent] = []
        self.board: BoardState | None = None
        """`None` until the first round lands, so the server can tell "no round
        has run yet" from "a round ran and found nothing wrong"."""

        self._issued: tuple | None = None
        self._degraded: str | None = None
        self._missing: frozenset[str] = frozenset()

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

        `frames_done` comes from the driver rather than the round because it is
        what has actually been written to disk — the only place on the page
        showing real pixels — while the forecast counts what the farm has told
        Grafana. Those can disagree, and the cards should show the frames.
        """
        applied = (surviving(result.decision.actions, result.guard_rejections)
                   if result.decision is not None else [])

        self._announce_misses(result, review, now_epoch_s)
        self._announce_degrade(result, now_epoch_s)
        self._issue(result.decision, applied, now_epoch_s)

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
