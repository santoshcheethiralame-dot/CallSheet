"""The three arms. Pure functions of (night, costs) -> RunResult.

The CALLSHEET arm imports `forecast_all`, `misses`, `rejected`, `surviving`,
`apply_actions` and `verify` from the package and drives them unchanged. If it
reimplemented the forecaster the ablation would be measuring the benchmark
rather than the product, and a bug fixed in one would silently survive in the
other.

The one thing it does not import is `callsheet.decide`. See the module docstring
of `bench` for why, and for what that costs the interpretation of the result.
"""

from __future__ import annotations

from callsheet.apply import apply_actions
from callsheet.decide import Action
from callsheet.domain import Shot
from callsheet.forecast import forecast_all, misses
from callsheet.guard import rejected, surviving
from callsheet.verify import verify

from bench.simulate import Progress, RunResult, run_night
from bench.workload import MEASURED_MS, CostTable, Night

ARMS = ("FIFO", "Priority-only", "CALLSHEET")


def _prior(night: Night) -> dict[tuple[str, str], float]:
    """Previous nights' measured rates, per (shot, tier).

    Every shot has history, so the forecaster reports `estimate_source
    ="observed"` throughout and the 8-second fallback is never exercised here.
    That is the favourable reading for CALLSHEET and it is stated rather than
    hidden: a farm with no history for a shot forecasts worse than this.
    """
    return {
        (shot.id, quality): MEASURED_MS[night.shot_class[shot.id]][quality]
        for shot in night.shots
        for quality in ("final", "proxy")
    }


def fifo(night: Night, costs: CostTable) -> RunResult:
    """Render in manifest order. React to nothing."""
    return run_night(night, costs, list(night.shots))


def priority_only(night: Night, costs: CostTable) -> RunResult:
    """Order once by static shot priority, highest first. React to nothing.

    Ties keep manifest order — `sorted` is stable — so this arm differs from
    FIFO only where priorities differ. It never looks at the deadline, at what
    the review requires, at the cut list, or at anything the farm has done.
    """
    order = sorted(night.shots, key=lambda shot: -shot.priority)
    return run_night(night, costs, order)


def choose_sacrifices(queue: list[Shot], night: Night, now_s: int,
                      state) -> list[Action]:
    """Stand-in for the model: the cheapest structurally valid sacrifice.

    NOT AN AGENT, AND NOT A MODEL. This is a deterministic rule, and the choice
    it makes is the easy half of the problem: it takes the sacrifices in a fixed
    order (director-cut shots first, then lowest priority) and stops as soon as
    `verify` says the gap is closed. What the model is actually for — weighing
    two sacrifices that both close the gap but cost the production different
    things, or noticing that the cheap arithmetic answer is unacceptable for a
    reason nobody encoded — is not exercised, not measured, and not claimed.

    Everything around the choice is the real thing: `forecast_all` decides who
    is at risk, `rejected`/`surviving` police the actions, and `verify` decides
    when to stop.
    """
    forecasts = forecast_all(queue, night.review, state, now_s)
    missing = misses(forecasts)
    if not missing:
        return []

    # The same anchor `run_round` uses: the earliest missing required shot.
    at_risk = missing[0].shot_id
    position = {forecast.shot_id: index for index, forecast in enumerate(forecasts)}

    remaining = {forecast.shot_id: forecast.frames_remaining for forecast in forecasts}
    candidates = [
        shot for shot in queue
        if position[shot.id] < position[at_risk]
        and shot.id not in night.review.required_shots
        # A shot with nothing left to render is already paid for. Preempting it
        # returns no time and only puts a sacrifice on the call sheet that costs
        # the production a shot for free.
        and remaining[shot.id] > 0
    ]
    # Cut shots go first: work the director has already thrown away is free to
    # give up. After that, lowest priority, then queue order for determinism.
    candidates.sort(key=lambda shot: (not shot.is_cut, shot.priority, position[shot.id]))

    def closes(actions: list[Action]) -> bool:
        allowed = surviving(actions, rejected(actions, forecasts, at_risk))
        residuals = verify(queue, allowed, night.review, state, now_s)
        return bool(residuals) and all(residual.closed for residual in residuals)

    actions: list[Action] = []
    for shot in candidates:
        actions.append(Action(
            shot.id, "preempt",
            f"{shot.id} is ahead of {at_risk} in the queue and is not required "
            f"for {night.review.name}",
        ))
        if closes(actions):
            return actions

    # Nothing ahead was enough. Make the at-risk shot itself cheaper — the one
    # action the guard allows on the at-risk shot, because it recovers time
    # wherever the shot sits.
    #
    # Kept only if it closes the gap. §14 measured the proxy speedup at 1.08x for
    # a cheap shot, so a downgrade that still misses the review is a shot nobody
    # will watch, rendered worse: a quality loss bought with nothing. When the
    # downgrade does not save the shot, the honest move is to escalate at full
    # quality and let a human decide.
    at_risk_shot = next(shot for shot in queue if shot.id == at_risk)
    if at_risk_shot.quality != "proxy":
        downgrade = Action(
            at_risk, "downgrade",
            f"{at_risk} cannot be saved by the queue ahead of it, so it goes to "
            "proxy to make the review at all",
        )
        if closes(actions + [downgrade]):
            return actions + [downgrade]

    # Still short. Say so rather than pretending the plan works.
    actions.append(Action(at_risk, "escalate", f"{at_risk} cannot be saved; wake someone"))
    return actions


def plan(queue: list[Shot], night: Night, now_s: int, state) -> list[Action]:
    """One round of the CALLSHEET arm: propose, then police. Returns what the
    guard let through — the actions that would actually be applied.

    Separated from `choose_sacrifices` so the guard sits on the boundary the
    product puts it on, in front of *whatever* proposed the actions. In the
    product that is Gemini; here it is a rule; the guard cannot tell and does
    not need to.
    """
    forecasts = forecast_all(queue, night.review, state, now_s)
    missing = misses(forecasts)
    if not missing:
        return []
    proposed = choose_sacrifices(queue, night, now_s, state)
    return surviving(proposed, rejected(proposed, forecasts, missing[0].shot_id))


def callsheet(night: Night, costs: CostTable) -> RunResult:
    """Forecast against the deadline every round, then preempt and downgrade.

    Starts from manifest order, exactly as the product does — reordering the
    queue is not in `apply_actions`' repertoire, so giving this arm a sort the
    real system cannot perform would make the win unreproducible in the product.
    """
    prior = _prior(night)

    def replan(queue: list[Shot], progress: Progress) -> tuple[list[Shot], int]:
        state = progress.farm_state(prior)
        allowed = plan(queue, night, int(progress.clock_ms // 1000), state)
        escalations = sum(1 for action in allowed if action.action == "escalate")
        return apply_actions(queue, allowed), escalations

    return run_night(night, costs, list(night.shots), replan=replan)


POLICIES = {"FIFO": fifo, "Priority-only": priority_only, "CALLSHEET": callsheet}


def run_arm(name: str, night: Night, costs: CostTable) -> RunResult:
    return POLICIES[name](night, costs)
