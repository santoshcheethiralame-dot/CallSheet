"""Rejects actions that cannot possibly help, before they are applied.

Phase 2 conveyed queue position to the model through the prompt. A prompt is a
request, not a constraint: a model that ignores it produces an action the code
happily applies and then annotates as a fix. This makes the invariant
enforceable, and it is structural — it asks only "could this recover time for
the at-risk shot at all", never "is this the best move". Judgement stays with
the model; impossibility is arithmetic.
"""

from __future__ import annotations

from callsheet.decide import Action
from callsheet.forecast import Forecast


def rejected(
    actions: list[Action], forecasts: list[Forecast], at_risk_shot_id: str
) -> list[tuple[Action, str]]:
    """Return `(action, why)` for every action that cannot recover time.

    `forecasts` is in render order, so its index *is* queue position.
    """
    order = {forecast.shot_id: index for index, forecast in enumerate(forecasts)}
    at_risk = order.get(at_risk_shot_id)

    problems: list[tuple[Action, str]] = []
    for action in actions:
        if action.action == "escalate":
            # Escalation is always available. It is the honest move when nothing
            # else is left, and rejecting it would leave the agent mute.
            continue

        index = order.get(action.shot_id)
        if index is None:
            problems.append((action, f"unknown shot {action.shot_id}"))
            continue

        # Ordered above the position checks on purpose: downgrading the at-risk
        # shot makes *that shot* cheaper, so it recovers time wherever the shot
        # sits — including last in the queue, where every other lever is dead.
        if action.action == "downgrade" and action.shot_id == at_risk_shot_id:
            continue

        if at_risk is None:
            # No anchor to reason about position against. Don't invent one.
            continue

        if action.shot_id == at_risk_shot_id:
            problems.append((action, f"cannot {action.action} the at-risk shot itself"))
        elif index > at_risk:
            problems.append((
                action,
                f"{action.shot_id} is behind {at_risk_shot_id} in the queue, "
                "so changing it recovers no time",
            ))

    return problems


def surviving(
    actions: list[Action], rejections: list[tuple[Action, str]]
) -> list[Action]:
    """The actions the guard let through — the ones that are actually taken.

    Identity, not equality. `Action` is a frozen dataclass, so two distinct
    actions with identical fields compare equal, and an equality filter would
    drop a legitimate action that merely matched a rejected one.
    """
    blocked = {id(action) for action, _ in rejections}
    return [action for action in actions if id(action) not in blocked]
