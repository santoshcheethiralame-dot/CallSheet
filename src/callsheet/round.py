"""One scheduling round: observe, forecast, and only then judge."""

from __future__ import annotations

from dataclasses import dataclass

from callsheet.annotate import write_annotation
from callsheet.config import Config
from callsheet.decide import Decision, decide
from callsheet.domain import Review, Shot
from callsheet.farm_state import read_farm_state
from callsheet.forecast import Forecast, forecast_all, misses


@dataclass(frozen=True)
class RoundResult:
    forecasts: list[Forecast]
    decision: Decision | None
    annotation_written: bool
    degraded_reason: str | None = None


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
    if not misses(forecasts):
        return RoundResult(forecasts, None, False)

    try:
        decision = decide(config, shots, review, forecasts)
    except Exception as error:      # noqa: BLE001 — degrade, never crash the loop
        return RoundResult(forecasts, None, False, degraded_reason=str(error))

    # The write is wrapped too. A Grafana blip must not discard a forecast and a
    # decision that both succeeded — that is the same failure class the model
    # call is already protected against.
    try:
        await write_annotation(config, decision, now_epoch_s)
    except Exception as error:      # noqa: BLE001
        return RoundResult(forecasts, decision, False,
                           degraded_reason=f"annotation failed: {error}")

    return RoundResult(forecasts, decision, True)
