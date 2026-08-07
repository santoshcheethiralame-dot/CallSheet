"""Writes the agent's decision back into Grafana as an annotation.

This is what makes the observability integration load-bearing rather than
decorative: the agent reads farm state out of Grafana and puts its reasoning
back in, on the same timeline as the metrics that provoked it.
"""

from __future__ import annotations

from callsheet.config import Config
from callsheet.decide import Action, Decision
from callsheet.grafana_mcp import call_tool
from callsheet.verify import Residual


def build_annotation(
    decision: Decision,
    now_epoch_s: int,
    residuals: list[Residual] | None = None,
    applied: list[Action] | None = None,
    rejections: list[tuple[Action, str]] | None = None,
) -> dict:
    """Render the decision as Grafana sees it.

    `applied` defaults to every action in the decision, but when the guard has
    blocked some, only the surviving ones are reported as taken. A rejected
    action that still shows up on the timeline is the same dishonesty as an
    unreported residual, one level down.
    """
    actions = decision.actions if applied is None else applied
    detail = "; ".join(
        f"{action.action} {action.shot_id} ({action.reason})" for action in actions
    )
    text = f"CALLSHEET: {decision.summary}"
    if detail:
        text = f"{text} — {detail}"

    for action, why in rejections or []:
        text += f" — REJECTED {action.action} {action.shot_id}: {why}"

    # The reason this file changed at all. An annotation that records what the
    # agent meant to do, with no word on whether it worked, is the Phase 2 bug
    # written to a dashboard where a human will believe it.
    unclosed = [residual for residual in (residuals or []) if not residual.closed]
    if unclosed:
        shortfalls = ", ".join(
            f"{residual.shot_id} still short by {residual.shortfall_s}s"
            for residual in unclosed
        )
        text += f" — GAP NOT CLOSED: {shortfalls}"

    return {
        "text": text,
        # Grafana takes epoch *milliseconds*. Handing it seconds is accepted
        # without complaint and files the annotation in January 1970, where no
        # default time range will ever show it.
        "time": now_epoch_s * 1000,
        "tags": ["callsheet", "scheduling-decision"],
    }


async def write_annotation(
    config: Config,
    decision: Decision,
    now_epoch_s: int,
    residuals: list[Residual] | None = None,
    applied: list[Action] | None = None,
    rejections: list[tuple[Action, str]] | None = None,
) -> str:
    """Takes the same arguments as `build_annotation` and forwards them all.

    Not a convenience: a writer that cannot carry a residual would leave the
    builder's gap reporting unreachable from production, green unit test and all.
    """
    return await call_tool(
        config,
        "create_annotation",
        build_annotation(decision, now_epoch_s, residuals, applied, rejections),
    )
