# CALLSHEET

A render farm that reports to Grafana, and an agent that turns its telemetry into
production decisions — which shots get sacrificed so the morning review isn't missed.

Built for the Google Cloud Agentic Cinema Hackathon, Grafana partner track.

## Status

Phase 4 — the loop checks its own work, and there is a board to watch it on.
Real Blender renders report to Grafana
Cloud over OpenTelemetry; the agent reads their observed frame times back through
the Grafana MCP server, forecasts which shots miss the review deadline, asks
Gemini what to sacrifice, re-forecasts the queue as it would be after that plan
lands, and writes both the call sheet and the leftover shortfall back to Grafana
as an annotation.

## How it works

1. **Observe** — read measured per-frame render times out of Grafana, keyed by
   shot *and* quality tier.
2. **Forecast** — walk the serial queue in render order and compute which
   required shots finish after the review deadline.
3. **Decide** — only on a miss, ask Gemini what production should give up. A
   structural guard then rejects any action that cannot recover time for the
   at-risk shot, before it is applied.
4. **Verify** — apply the surviving actions to a copy of the queue, re-run the
   forecast, and report what is *still* missing.

**If the plan does not close the gap, the system says so** — in the terminal and
in the Grafana annotation — rather than reporting the decision as a success.
A coordinator who says "fixed" when they have not is worse than one who says
"this cannot be saved, wake someone."

## How the loop divides the work

**Code does the arithmetic. The model does the judgement.** `forecast.py` imports
nothing from `decide.py` and never touches the network — every duration, ETA and
missed-deadline verdict is computed there, in Python, and handed to the model as
a fixed fact. Gemini is asked one question only: given this shortfall, what
should production give up? A number invented by the model would be a defect.

Gemini is called *only* when the forecast shows a miss, so a healthy farm costs
zero model calls — enforced by a test, not by good intentions.

The farm is a single serial queue: one shot renders at a time, in manifest order.
The prompt states that ordering explicitly, because without it the model proposes
sacrificing shots that sit *behind* the shot they are meant to rescue — a saving
that recovers nothing.

## Setup

1. Install Blender 4.2+ (verified on 5.2.0 LTS) and Python 3.11+.
2. Install the `mcp-grafana` binary from https://github.com/grafana/mcp-grafana/releases
3. Create a free Grafana Cloud stack (no credit card required).
4. `copy .env.example .env` and fill in every value.
5. `pip install -e ".[dev]"`
6. `blender -b -P scenes/make_scenes.py`

`MCP_GRAFANA_PATH` is optional — set it only when the `mcp-grafana` binary is not
on `PATH`; otherwise the bare command `mcp-grafana` is used.

## Run

```
python -m uvicorn callsheet.server:app --port 8420   # the shot board, at localhost:8420
python scripts/spike_end_to_end.py    # render, and prove the telemetry lands
python scripts/demo_round.py          # one full scheduling round, verified
python scripts/fresh_night.py         # clear the frames and the queue, to start a night over
```

The board is the product surface: the call sheet itself, with each shot as a
numbered row carrying its last rendered frame. When the agent issues an
amendment the sheet is reissued on the next colour of paper — white, blue, pink,
goldenrod — which is what a film production does when the day is revised.

`spike_end_to_end.py` exits 0 when real render telemetry is reaching Grafana
Cloud and is readable back through the MCP server.

`demo_round.py` exits 0 when the whole loop completed against live Grafana and
live Gemini **and reported the truth about the outcome** — not when the deadline
was saved. It sets a deliberately tight deadline: `review.json` carries
`deadline_epoch_s: 0` as a sentinel and the script replaces it with `now + 30s`,
which SH003 cannot make. Preempting SH002 is the right move and is still not
enough, so the run ends like this:

```
Review 'Director review' in 30s, requires ['SH001', 'SH003']
  ok   SH001: 3 frames, 12.9s predicted (observed)
  ok   SH002: 3 frames, 19.4s predicted (observed)
  MISS SH003: 3 frames, 82.0s predicted (observed)

CALL SHEET: I am preempting non-required shot SH002 to clear the queue for
required shot SH003 to make the director review deadline.
  PREEMPT    SH002 — SH002 sits ahead of SH003 in the queue and is not required
             for this review, so preempting it recovers the render time needed.

After applying the plan:
  CLOSED  SH001 makes the review
  STILL SHORT  SH003 by 65s

PASS: loop completed and reported honestly — gap NOT closed (SH003 by 65s).
```

Run `spike_end_to_end.py` first: the rate query reads a one-hour window, so
without recent renders every forecast falls back to the 8-second default and the
figures above read `(fallback)` instead of `(observed)`.

`(observed)` versus `(fallback)` marks whether the per-frame figure came from
measured telemetry or from the 8-second default; a guess must never be able to
pass itself off as a measurement. The call sheet, any guard-rejected actions and
the residual shortfall are then written to Grafana as one annotation tagged
`callsheet`, so the dashboard carries the same caveat the terminal does.

## Test

```
python -m pytest              # unit tests, no Blender or credentials needed
python -m pytest -m integration   # requires Blender and a .env
```

## License

Apache-2.0
