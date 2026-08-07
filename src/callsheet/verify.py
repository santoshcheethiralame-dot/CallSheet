"""Did the decision actually work? Pure arithmetic, never the model's job.

This is the file the whole phase exists for. Without it the system prints PASS
after a decision that leaves a required shot a minute late, because it reports
the *intent* of the plan rather than its outcome. Re-forecasting the queue as it
would be after the actions land is the only way to state a residual honestly,
and a coordinator who says "fixed" when they have not is worse than one who says
"this cannot be saved, wake someone".
"""

from __future__ import annotations

from dataclasses import dataclass

from callsheet.apply import apply_actions
from callsheet.decide import Action
from callsheet.domain import FarmState, Review, Shot
from callsheet.forecast import forecast_all


@dataclass(frozen=True)
class Residual:
    shot_id: str
    shortfall_s: int
    """Seconds still missing after the plan. `0` when the gap closed, `-1` when
    the shot was preempted out of the queue entirely — a sentinel rather than a
    number, because there is no finite lateness for work that will never run."""

    closed: bool


def verify(
    shots: list[Shot],
    actions: list[Action],
    review: Review,
    state: FarmState,
    now_epoch_s: int,
) -> list[Residual]:
    """Re-forecast the queue as it would be after the actions are applied.

    Only the review's required shots are reported: a sacrificed shot finishing
    late is the plan working, not the plan failing.
    """
    after = apply_actions(shots, actions)
    forecasts = forecast_all(after, review, state, now_epoch_s)
    by_id = {forecast.shot_id: forecast for forecast in forecasts}

    residuals = []
    for shot_id in review.required_shots:
        forecast = by_id.get(shot_id)
        if forecast is None:
            # A required shot was preempted — the worst possible outcome, and
            # never "closed": the deadline is met by an absence.
            residuals.append(Residual(shot_id, shortfall_s=-1, closed=False))
            continue
        shortfall = max(0, forecast.finishes_at_epoch_s - review.deadline_epoch_s)
        residuals.append(Residual(shot_id, shortfall, closed=shortfall == 0))

    return residuals
