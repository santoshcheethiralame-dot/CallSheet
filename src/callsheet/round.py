"""One scheduling round: observe, forecast, and only then judge."""

from __future__ import annotations

from dataclasses import dataclass, field

from callsheet.annotate import write_annotation
from callsheet.config import Config
from callsheet.decide import Action, Decision, decide
from callsheet.domain import Review, Shot
from callsheet.farm_state import read_farm_state
from callsheet.forecast import Forecast, forecast_all, misses
from callsheet.guard import rejected, surviving
from callsheet.verify import Residual, verify


@dataclass(frozen=True)
class RoundResult:
    forecasts: list[Forecast]
    decision: Decision | None
    annotation_written: bool
    degraded_reason: str | None = None
    residuals: list[Residual] = field(default_factory=list)
    """What is *still* missing after the plan is applied. Empty on the paths
    where no plan was produced — a healthy farm or a failed model call — and
    never empty merely because the plan worked: a closed gap is a `Residual`
    with `closed=True`, not an absence."""

    guard_rejections: list[tuple[Action, str]] = field(default_factory=list)


async def run_round(config: Config, shots: list[Shot], review: Review,
                    now_epoch_s: int) -> RoundResult:
    """Observe the farm, forecast the deadline, and judge only if it is missed.

    `shots` is passed to the forecaster in the order given, and that order is
    the render order — see `forecast_all`.
    """
    state = await read_farm_state(config)
    forecasts = forecast_all(shots, review, state, now_epoch_s)

    # A healthy farm costs zero model calls. That is not an optimisation, it is
    # what keeps the whole system inside the Gemini free tier.
    missing = misses(forecasts)
    if not missing:
        return RoundResult(forecasts, None, False)

    try:
        decision = decide(config, shots, review, forecasts)
    except Exception as error:      # noqa: BLE001 — degrade, never crash the loop
        return RoundResult(forecasts, None, False, degraded_reason=str(error))

    # Anchor the guard on the EARLIEST missing required shot. `misses` returns
    # queue order, so this is deliberately conservative: an action that would
    # rescue a later missing shot is rejected as "behind". A policy choice,
    # written down rather than arrived at by accident.
    at_risk = missing[0].shot_id

    guard_rejections = rejected(decision.actions, forecasts, at_risk)
    allowed = surviving(decision.actions, guard_rejections)
    residuals = verify(shots, allowed, review, state, now_epoch_s)

    # The write is wrapped too. A Grafana blip must not discard a forecast and a
    # decision that both succeeded — that is the same failure class the model
    # call is already protected against.
    try:
        await write_annotation(config, decision, now_epoch_s,
                               residuals=residuals, applied=allowed,
                               rejections=guard_rejections)
    except Exception as error:      # noqa: BLE001
        return RoundResult(forecasts, decision, False,
                           f"annotation failed: {error}", residuals, guard_rejections)

    return RoundResult(forecasts, decision, True, None, residuals, guard_rejections)
