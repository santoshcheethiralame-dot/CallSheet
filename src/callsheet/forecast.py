"""Deterministic deadline arithmetic. No network, no clock, no model."""

from __future__ import annotations

import math
from dataclasses import dataclass

from callsheet.domain import FarmState, Review, Shot


@dataclass(frozen=True)
class Forecast:
    shot_id: str
    frames_remaining: int
    predicted_ms: float
    finishes_at_epoch_s: int
    misses_deadline: bool


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

    Per-shot mean frame duration is read from observed telemetry and already
    includes Blender's fixed startup cost for that shot, so no separate
    overhead term is modelled. Shots with no observed history fall back to
    `fallback_frame_ms`.

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
        per_frame = state.mean_frame_ms.get(shot.id, fallback_frame_ms)
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
            )
        )

    return forecasts


def misses(forecasts: list[Forecast]) -> list[Forecast]:
    return [forecast for forecast in forecasts if forecast.misses_deadline]
