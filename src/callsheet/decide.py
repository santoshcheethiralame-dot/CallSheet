"""The single place a language model is called.

It receives a completed forecast and makes the production judgement call.
It is never asked to work out a duration, an ETA, or whether a shot misses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from google import genai
from google.genai import types

from callsheet.config import Config
from callsheet.domain import Review, Shot
from callsheet.forecast import FALLBACK, Forecast

MODEL = "gemini-3.6-flash"
VALID_ACTIONS = {"preempt", "downgrade", "escalate"}

SYSTEM = """You are the production coordinator for a VFX render farm.

A render deadline is going to be missed. The shortfall has already been measured
for you. Every number below is a given fact. Do not revise it.

Decide which shots to sacrifice so the required shots make the review. Prefer
sacrificing shots the director has already cut. Never preempt a shot the review
requires. Downgrading a shot to proxy quality is acceptable for a review; it is
not acceptable for a final delivery.

Reply with JSON only:
{"summary": "<one sentence a coordinator would say out loud>",
 "actions": [{"shot_id": "...", "action": "preempt|downgrade|escalate", "reason": "..."}]}
"""


@dataclass(frozen=True)
class Action:
    shot_id: str
    action: str
    reason: str


@dataclass(frozen=True)
class Decision:
    summary: str
    actions: list[Action]


def build_prompt(shots: list[Shot], review: Review, forecasts: list[Forecast]) -> str:
    by_id = {shot.id: shot for shot in shots}
    lines = [
        f"Review: {review.name}",
        f"Deadline: epoch {review.deadline_epoch_s}",
        f"Required shots: {', '.join(review.required_shots)}",
        "",
        "Shortfalls below are rounded up to whole seconds. Treat them as given.",
        "",
    ]

    for forecast in forecasts:
        shot = by_id[forecast.shot_id]
        shortfall = forecast.finishes_at_epoch_s - review.deadline_epoch_s
        status = f"MISSES by {shortfall}s" if forecast.misses_deadline else "on time"
        # A prediction with no measurement behind it is a guess, and the
        # coordinator is entitled to know which of its facts are soft.
        confidence = " [no telemetry for this shot: figure is a default]" \
            if forecast.estimate_source == FALLBACK else ""
        lines.append(
            f"{shot.id}: {forecast.frames_remaining} frames left, "
            f"priority {shot.priority}, quality {shot.quality}, "
            f"{'CUT by the director' if shot.is_cut else 'in the edit'} — {status}"
            f"{confidence}"
        )

    return "\n".join(lines)


def parse_decision(raw: str) -> Decision:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    payload = json.loads(text.strip())

    actions = []
    for entry in payload.get("actions", []):
        action = entry["action"]
        if action not in VALID_ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        actions.append(Action(entry["shot_id"], action, entry.get("reason", "")))

    return Decision(summary=payload["summary"], actions=actions)


def decide(config: Config, shots: list[Shot], review: Review,
           forecasts: list[Forecast]) -> Decision:
    client = genai.Client(api_key=config.gemini_api_key)
    response = client.models.generate_content(
        model=MODEL,
        contents=build_prompt(shots, review, forecasts),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    return parse_decision(response.text)
