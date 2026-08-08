# CALLSHEET

A render farm that reports to Grafana, and an agent that turns its telemetry into
production decisions — which shots get sacrificed so the morning review isn't missed.

Built for the Google Cloud Agentic Cinema Hackathon, Grafana partner track.

## Status

Phase 5 — the loop checks its own work, there is a board to watch it on, and
there is a measured result behind the scheduler.
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

## The scheduler ablation

```
python -m bench.run
```

Three scheduling policies over one workload: **FIFO** (manifest order, reacts to
nothing), **priority-only** (sorted once by static shot priority, still reacts to
nothing), and **CALLSHEET** (forecast against the deadline from observed
per-frame costs, then preempt non-required shots ahead of an at-risk required
shot, or downgrade the at-risk shot itself).

16 shots a night, 6 required by the review, deadline at 1.25x the required
shots' final-quality work. 200 runs per arm — 5 generated nights x 40 draws of
render cost. Cells are mean ± stdev with min–max in brackets.

| Scheduler | Required shots delivered | Deadline misses | Node-seconds on cut shots |
|---|---|---|---|
| FIFO | 2.65 ± 0.87 (1–5) | 3.35 ± 0.87 (1–5) | 95.0 ± 50.6 |
| Priority-only | 3.63 ± 1.38 (2–6) | 2.37 ± 1.38 (0–4) | 82.2 ± 57.9 |
| **CALLSHEET** | **5.96 ± 0.18 (5–6)** | **0.04 ± 0.18 (0–1)** | **11.8 ± 17.6** |

**What it does not measure: the model.** Gemini's free tier is 20 requests per
day, so a few hundred runs cannot call it and this harness does not — `bench`
never imports `decide`, and an AST check in the tests enforces that rather than
a promise in a README. What it measures is the **scheduling policy the agent
operates within**: the forecaster, the guard, the verifier and the
preempt/downgrade repertoire, all imported from the package and driven unchanged
so a regression in the product is a regression in the benchmark. The choice of
*which* shot to sacrifice is made here by a deterministic rule, and that is the
easy half of the problem. Weighing two sacrifices that both close the gap but
cost the production different things is the model's actual job, and nothing here
evaluates it.

Render costs are not Blender either. They are sampled from the per-shot means
measured in the design doc, with the 1.5x run-to-run swing and ±3.5% within-run
spread also measured there. Whole runs are repeated rather than frames, because
the measurements say the noise lives between process launches. Every arm in a
given run sees identical sampled costs — including for tiers it did not choose —
so the arms are compared against the same luck, and a seed reproduces a table
exactly.

**What the win costs, and where CALLSHEET loses.** It finishes **1.20
non-required shots against FIFO's 5.93**: the deadline is met by destroying work
the farm had started, and tomorrow's review may want it. Set every shot required
and the deadline out of reach (`--required-fraction 1.0 --slack 0.85`) and the
advantage disappears — **CALLSHEET ties FIFO exactly at 13.03 and loses to
priority-only's 13.91**, correctly declining to act and escalating 17 times a
night instead. Priority-only wins there on a lever CALLSHEET does not have:
reordering the queue. `apply_actions` can preempt and downgrade, not resequence.

Both facts are pinned by tests, and §6 of the design doc carries the full
methodology, the failure case, and the two other places a hostile reading lands.

## Test

```
python -m pytest              # unit tests, no Blender or credentials needed
python -m pytest -m integration   # requires Blender and a .env
```

## License

Apache-2.0
