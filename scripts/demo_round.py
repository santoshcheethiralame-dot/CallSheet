"""Phase 3 gate: observe the real farm, forecast a miss, decide, then check the decision.

Run:
    python scripts/demo_round.py

Exit 0 means the loop ran against live Grafana and live Gemini *and reported the
truth about the outcome*. It does not mean the deadline was saved. A gap the
plan cannot close is a correct result here; the same gap printed as success is
the bug this phase exists to remove.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time

from dotenv import load_dotenv

from callsheet.config import Config, ConfigError
from callsheet.domain import load_review, load_shots
from callsheet.guard import surviving
from callsheet.round import run_round
from callsheet.verify import verdict

DEADLINE_SECONDS = 30      # tight on purpose: SH003 alone takes ~80s


def main() -> int:
    load_dotenv()
    try:
        config = Config.from_env(os.environ)
    except ConfigError as error:
        print(f"FAIL: {error}")
        return 2

    now = int(time.time())
    shots = load_shots("scenes/manifest.json")
    # review.json carries deadline_epoch_s = 0 as a sentinel. A committed
    # absolute deadline would be in the past by the next day and the demo would
    # stop being reproducible, so the clock is applied here instead.
    review = dataclasses.replace(load_review("review.json"),
                                 deadline_epoch_s=now + DEADLINE_SECONDS)

    print(f"Review '{review.name}' in {DEADLINE_SECONDS}s, requires {review.required_shots}")
    result = asyncio.run(run_round(config, shots, review, now_epoch_s=now))

    for forecast in result.forecasts:
        state = "MISS" if forecast.misses_deadline else "ok  "
        # Provenance is printed because a judge watching the demo cannot
        # otherwise tell a measured prediction from an 8-second default.
        print(f"  {state} {forecast.shot_id}: {forecast.frames_remaining} frames, "
              f"{forecast.predicted_ms / 1000:.1f}s predicted ({forecast.estimate_source})")

    if result.degraded_reason:
        print(f"DEGRADED: {result.degraded_reason}")
        return 4

    if result.decision is None:
        print("FAIL: no miss forecast - the deadline was not tight enough to exercise the agent")
        return 3

    # Only what the guard let through. Printing every proposed action here put a
    # rejected one in the call sheet as though it had been taken, contradicting
    # both the REJECTED lines below it and the annotation Grafana receives.
    print(f"\nCALL SHEET: {result.decision.summary}")
    for action in surviving(result.decision.actions, result.guard_rejections):
        print(f"  {action.action.upper():10} {action.shot_id} - {action.reason}")

    # A guard that fires silently teaches a viewer nothing. Rejected actions are
    # printed because "the model proposed this and the code refused it" is the
    # part of the design that is otherwise invisible.
    for action, why in result.guard_rejections:
        print(f"  REJECTED   {action.shot_id} - {why}")

    print("\nAfter applying the plan:")
    for residual in result.residuals:
        if residual.closed:
            print(f"  CLOSED  {residual.shot_id} makes the review")
        else:
            print(f"  STILL SHORT  {residual.shot_id} by {residual.shortfall_s}s")

    print(f"\nannotation written: {result.annotation_written}")

    # Every branch is exit 0. The system's job here is to tell the truth about
    # the outcome, not to guarantee a good one. The sentence itself is built in
    # `verify.verdict` so it can be tested without running the demo by hand.
    print(f"\n{verdict(result.residuals)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
