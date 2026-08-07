"""Deterministic deadline arithmetic. No network, no clock, no model."""

from __future__ import annotations

import math
from dataclasses import dataclass

from callsheet.domain import FarmState, Review, Shot


OBSERVED = "observed"
FALLBACK = "fallback"


@dataclass(frozen=True)
class Forecast:
    shot_id: str
    frames_remaining: int
    predicted_ms: float
    finishes_at_epoch_s: int
    misses_deadline: bool
    estimate_source: str
    """`"observed"` if the per-frame figure came from measured telemetry,
    `"fallback"` if it is the global guess. Deliberately has no default: a
    forecast built from a guess must not be able to pass itself off as a
    measurement, and the whole point of this file is that its numbers are
    auditable."""


def forecast_all(
    shots: list[Shot],
    review: Review,
    state: FarmState,
    now_epoch_s: int,
    fallback_frame_ms: float = 8000.0,
) -> list[Forecast]:
    """Forecast completion for every shot, in queue order.

    The farm is a single queue, so each shot starts when the previous finishes
    and every shot carries the wait of everything ahead of it. **The result
    therefore depends on the order of `shots`**: reordering the list moves the
    accumulated wait onto different shots and can change which of them miss.
    That is the intended behaviour, not an accident of implementation — the
    caller's list order *is* the render order, and reordering the queue is one
    of the levers a scheduler has. Pass shots in the order they will render.

    Mean frame duration is read from observed telemetry keyed by
    `(shot, quality)` and already includes Blender's fixed startup cost for that
    shot, so no separate overhead term is modelled. The rate is looked up at the
    tier the shot will actually render at; failing that, at `final`; failing
    that, `fallback_frame_ms`. Only an exact tier match counts as
    `estimate_source="observed"` — both other paths are marked `"fallback"`, so
    a guess can never be mistaken for a measurement further down the pipeline.

    Completion times round *up* to the whole second. A forecaster that floors
    can report a shot finishing exactly on the deadline when it truly lands a
    fraction of a second late, and a missed deadline reported as on time is the
    one error this system must not make.
    """
    forecasts: list[Forecast] = []
    cursor_ms = 0.0

    for shot in shots:
        done = state.frames_done.get(shot.id, 0)
        remaining = max(0, len(shot.frames) - done)
        observed = state.mean_frame_ms.get((shot.id, shot.quality))
        if observed is not None:
            per_frame, source = observed, OBSERVED
        elif (shot.id, "final") in state.mean_frame_ms:
            # The shot has history, but not at the tier it is about to render.
            # The final rate is the best number available and still a guess:
            # proxy is measurably cheaper, so this over-estimates, and saying
            # "observed" would dress that guess up as a measurement.
            per_frame, source = state.mean_frame_ms[(shot.id, "final")], FALLBACK
        else:
            per_frame, source = fallback_frame_ms, FALLBACK

        predicted = remaining * per_frame

        cursor_ms += predicted
        finishes_at = now_epoch_s + math.ceil(cursor_ms / 1000)

        required = shot.id in review.required_shots
        forecasts.append(
            Forecast(
                shot_id=shot.id,
                frames_remaining=remaining,
                predicted_ms=predicted,
                finishes_at_epoch_s=finishes_at,
                misses_deadline=required and finishes_at > review.deadline_epoch_s,
                estimate_source=source,
            )
        )

    return forecasts


def misses(forecasts: list[Forecast]) -> list[Forecast]:
    return [forecast for forecast in forecasts if forecast.misses_deadline]
