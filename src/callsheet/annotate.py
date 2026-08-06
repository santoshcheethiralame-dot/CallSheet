"""Writes the agent's decision back into Grafana as an annotation.

This is what makes the observability integration load-bearing rather than
decorative: the agent reads farm state out of Grafana and puts its reasoning
back in, on the same timeline as the metrics that provoked it.
"""

from __future__ import annotations

from callsheet.config import Config
from callsheet.decide import Decision
from callsheet.grafana_mcp import call_tool


def build_annotation(decision: Decision, now_epoch_s: int) -> dict:
    detail = "; ".join(
        f"{action.action} {action.shot_id} ({action.reason})" for action in decision.actions
    )
    text = f"CALLSHEET: {decision.summary}"
    if detail:
        text = f"{text} — {detail}"
    return {
        "text": text,
        # Grafana takes epoch *milliseconds*. Handing it seconds is accepted
        # without complaint and files the annotation in January 1970, where no
        # default time range will ever show it.
        "time": now_epoch_s * 1000,
        "tags": ["callsheet", "scheduling-decision"],
    }


async def write_annotation(config: Config, decision: Decision, now_epoch_s: int) -> str:
    return await call_tool(config, "create_annotation", build_annotation(decision, now_epoch_s))
