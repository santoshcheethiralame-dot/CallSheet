"""One scheduling round: observe, forecast, and only then judge."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from callsheet.annotate import write_annotation
from callsheet.config import Config
from callsheet.decide import Action, Decision, decide, degrade_reason
from callsheet.domain import Review, Shot
from callsheet.farm_state import read_farm_state
from callsheet.forecast import Forecast, forecast_all, misses
from callsheet.guard import rejected, surviving
from callsheet.verify import Residual, verify

log = logging.getLogger(__name__)

ANNOTATION_FAILED = "The plan below is live, but Grafana did not record it"
"""Banner copy for the other degrade this file can produce. The model was fine
and the crew has real instructions, so borrowing the model's sentence here —
which the page used to staple onto every degrade — announced an outage that was
not happening while a working plan sat underneath it."""

Reuse = Callable[[list[Forecast], Review], Decision | None]
"""Whoever holds the memory across rounds, asked whether this situation has
already been judged. `Session.reuse` is the implementation; the round only knows
the shape, because a round that imported the session would be a round that
remembers, which is the thing this file is not."""


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
                    now_epoch_s: int,
                    frames_done: dict[str, int] | None = None,
                    reuse: Reuse | None = None) -> RoundResult:
    """Observe the farm, forecast the deadline, and judge only if it is missed.

    `shots` is passed to the forecaster in the order given, and that order is
    the render order — see `forecast_all`.

    `reuse` is asked, before the model is, whether this situation has already
    been judged; a decision it returns is used unchanged. It arrives as an
    argument for the same reason `frames_done` does — the round is one round and
    keeps nothing, so anything that spans rounds is held by the caller. Left out
    (the demo script, every direct test) the round simply always asks.

    `frames_done` is progress, and it arrives from the caller because the caller
    holds the job queue. `parse_farm_state` still refuses to report it (§13:
    telemetry answers *how fast*, the queue answers *what is left*) — the fix
    for the board contradicting itself is not to make the telemetry layer start
    guessing, it is to hand the forecaster the same number the cards are drawn
    from. One dict, merged here, read by the forecast and the verifier and then
    passed to `build_board` unchanged.
    """
    state = await read_farm_state(config)
    if frames_done is not None:
        state = dataclasses.replace(state, frames_done=dict(frames_done))
    forecasts = forecast_all(shots, review, state, now_epoch_s)

    # A healthy farm costs zero model calls. That is not an optimisation, it is
    # what keeps the whole system inside the Gemini free tier.
    missing = misses(forecasts)
    if not missing:
        return RoundResult(forecasts, None, False)

    # A miss that is the same miss as last round costs zero model calls too.
    # The farm changes far more slowly than the timer fires, and re-asking a
    # question already answered is how the day's 20 calls disappear before the
    # demo starts.
    standing = reuse(forecasts, review) if reuse is not None else None

    if standing is not None:
        decision = standing
    else:
        try:
            decision = decide(config, shots, review, forecasts)
        except Exception as error:  # noqa: BLE001 — degrade, never crash the loop
            # The banner gets production English; the operator gets the truth.
            log.warning("decide failed: %s", error)
            return RoundResult(forecasts, None, False,
                               degraded_reason=degrade_reason(error))

    # Anchor the guard on the EARLIEST missing required shot. `misses` returns
    # queue order, so this is deliberately conservative: an action that would
    # rescue a later missing shot is rejected as "behind". A policy choice,
    # written down rather than arrived at by accident.
    at_risk = missing[0].shot_id

    guard_rejections = rejected(decision.actions, forecasts, at_risk)
    allowed = surviving(decision.actions, guard_rejections)
    residuals = verify(shots, allowed, review, state, now_epoch_s)

    # The guard and the verifier run on a reused decision exactly as on a fresh
    # one — they are arithmetic against the clock, and their answers move even
    # when the plan does not. The annotation does not: it records the moment a
    # decision was taken, and that moment has already been written. Re-posting
    # it every 30s would fill the Grafana timeline with copies of one event and
    # make the dashboard read as a system deciding over and over.
    if standing is not None:
        return RoundResult(forecasts, decision, False, None,
                           residuals, guard_rejections)

    # The write is wrapped too. A Grafana blip must not discard a forecast and a
    # decision that both succeeded — that is the same failure class the model
    # call is already protected against.
    try:
        await write_annotation(config, decision, now_epoch_s,
                               residuals=residuals, applied=allowed,
                               rejections=guard_rejections)
    except Exception as error:      # noqa: BLE001
        log.warning("annotation failed: %s", error)
        return RoundResult(forecasts, decision, False,
                           f"{ANNOTATION_FAILED}: {error}", residuals, guard_rejections)

    return RoundResult(forecasts, decision, True, None, residuals, guard_rejections)
