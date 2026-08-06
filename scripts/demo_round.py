"""Phase 2 gate: observe the real farm, forecast a miss, let Gemini decide, annotate.

Run:
    python scripts/demo_round.py
Exit 0 means the full loop worked against live Grafana and live Gemini.
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
from callsheet.round import run_round

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
        print("FAIL: no miss forecast — the deadline was not tight enough to exercise the agent")
        return 3

    print(f"\nCALL SHEET: {result.decision.summary}")
    for action in result.decision.actions:
        print(f"  {action.action.upper():10} {action.shot_id} — {action.reason}")
    print(f"\nannotation written: {result.annotation_written}")
    print("\nPASS: observe -> forecast -> decide -> annotate completed end to end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
