# CALLSHEET

A render farm that reports to Grafana, and an agent that turns its telemetry into
production decisions — which shots get sacrificed so the morning review isn't missed.

Built for the Google Cloud Agentic Cinema Hackathon, Grafana partner track.

## Status

Phase 2 — the scheduling loop. Real Blender renders report to Grafana Cloud over
OpenTelemetry; the agent reads their observed frame times back through the
Grafana MCP server, forecasts which shots miss the review deadline, asks Gemini
what to sacrifice, and writes the call sheet back to Grafana as an annotation.

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
python scripts/spike_end_to_end.py    # Phase 1: render and prove the telemetry lands
python scripts/demo_round.py          # Phase 2: one full scheduling round
```

`spike_end_to_end.py` exits 0 when real render telemetry is reaching Grafana
Cloud and is readable back through the MCP server.

`demo_round.py` exits 0 when the whole loop completed against live Grafana and
live Gemini. It sets a deliberately tight deadline — `review.json` carries
`deadline_epoch_s: 0` as a sentinel and the script replaces it with
`now + 30s`, which SH003 (~80s of render) cannot make:

```
Review 'Director review' in 30s, requires ['SH001', 'SH003']
  ok   SH001: 3 frames, 15.4s predicted (observed)
  ok   SH002: 3 frames, 22.0s predicted (observed)
  MISS SH003: 3 frames, 80.4s predicted (observed)

CALL SHEET: I am preempting SH002 because it is ahead of SH003 in the queue
and not required for today's director review.
```

`(observed)` versus `(fallback)` marks whether the per-frame figure came from
measured telemetry or from the 8-second default; a guess must never be able to
pass itself off as a measurement. The decision is then written to Grafana as an
annotation tagged `callsheet`.

## Test

```
python -m pytest              # unit tests, no Blender or credentials needed
python -m pytest -m integration   # requires Blender and a .env
```

## License

Apache-2.0
