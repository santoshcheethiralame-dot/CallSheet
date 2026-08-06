# CALLSHEET — design

**Date:** 2026-08-07
**Event:** Google Cloud Agentic Cinema Hackathon (Devpost), Grafana partner track
**Deadline:** 2026-09-07, 14:00 PT — 31 days
**Builder:** solo
**Status:** Phase 1 complete — telemetry spine proven end to end (7 Aug 2026)

---

## 1. What it is

A render farm doing real work, watched through Grafana, with a Gemini agent that
translates infrastructure telemetry into **production decisions** — which shots
get sacrificed so the morning review isn't missed.

> A VFX coordinator queues 340 shots and goes to bed. At 7am she finds the farm
> spent the night re-rendering a shot the director already cut, while the one
> shot due in the 9am review died at 2am — and nobody knew.

## 2. Why this shape

Two things separate this from the field on a Grafana track.

**Telemetry from real work.** Most entrants will emit fake metrics from a toy app
and have Gemini narrate an alert. CALLSHEET renders actual frames and reports
actual frame times, memory spikes, and crashes.

**Observability as a planning substrate, not an alerting one.** Alert-explainers
are the obvious build. The non-obvious move is the unit of reasoning: telemetry
is infrastructure-shaped (nodes, jobs, CPU), a deadline is creative-shaped (shot
114 must be in the 9am review). Bridging those is the actual job of a post
coordinator, and nobody builds it.

## 3. Constraints

| Constraint | Resolution |
|---|---|
| $0 total cost, no credit card | Grafana Cloud free plan (permanent, no card): 10k series, 50 GB logs, 50 GB traces, 3 users, 14-day retention. Gemini via AI Studio free tier (permanent, no card). Blender, OTel, FastAPI, SQLite all free. |
| Gemini free-tier quota | **3.6 Flash: ~15 RPM / 1500 RPD.** 2.5 Flash and 2.5 Flash-Lite return 404 "no longer available to new users" — the model this spec originally named cannot be called at all. Architecture still keeps the LLM to a handful of calls per scheduling round, not one per metric. |
| Partner requirement | Grafana MCP server (`mcp-grafana`) connected and called at runtime. AI Observability added as a complement, which the rules note does not satisfy the requirement alone. |
| Google Cloud requirement | `google-adk` + `google-genai` imported and called; Cloud Run hosts the board. |
| New work only | Fresh repo, no reuse from Forge / Chronos / Hemlock / Untangle. |
| Hosted URL for judging | Cloud Run, inside the $300 trial / $100 hackathon credit reserve. |

## 4. Domain model

Four objects, deliberately few.

- **Shot** — `SH114`; sequence, frame range, priority, quality tier (`proxy` | `final`), status
- **Review** — a deadline with a required shot list (*Director review, 09:00: SH108, SH114, SH121*)
- **Job** — one shot at one quality, split into frame chunks
- **Worker** — a render node

The product lives entirely in the gap between **Worker** (what Grafana can see)
and **Review** (what the human cares about).

## 5. Architecture

```
Blender workers ──OTel──► Grafana Cloud ◄──MCP── ADK agent (Gemini 3.6 Flash)
      │                    (metrics/logs/traces)         │
      │                                                  │ requeue / preempt
      ▼                                                  ▼
  SQLite job queue ◄──────────────────────────── production tools
      │
      ▼
  FastAPI + SSE ──► shot board (Cloud Run)
```

### 5.1 Real-work layer

Three worker processes running Blender in background mode, pulling jobs from a
SQLite queue. Scenes are chosen for genuinely varying render cost.

**Decision:** do not bet on the *Sintel* / *Tears of Steel* production files.
They are multi-gigabyte and version-brittle, and famous pixels are not worth a
week of asset wrangling. What the judges score is that the telemetry is real.
Week 1 confirms asset choice either way; the fallback is procedurally generated
scenes of varying cost, which still produce real CPU, real time, real variance.

### 5.2 Telemetry layer

OpenTelemetry Python SDK → OTLP → Grafana Cloud.

- **Metrics** — `render.frame.duration` (histogram; labels: shot, sequence, quality, worker), `render.frame.memory`, `queue.depth`, `worker.busy`
- **Logs** — raw Blender stdout/stderr, so real failures (OOM, missing texture) arrive as real log lines
- **Traces** — span per job, child span per frame

Three workers at a few series each sits far below the 10k active-series ceiling.
14-day retention is irrelevant at demo timescales.

### 5.3 Agent layer

Google ADK agent on Gemini 3.6 Flash via `google-genai`. Two tool families:

1. **Grafana MCP** (`mcp-grafana`, service-account token) — the load-bearing
   partner integration. Queries Prometheus and Loki, reads alert rules, and
   **writes back** annotations.
2. **Production tools** (plain code, no AI) — `get_shot_manifest()`,
   `get_review_schedule()`, `requeue(shot, quality)`, `preempt(shot)`

The loop runs on a **timer — one scheduling round every 30s** — with a Grafana
alert webhook as an optional fast path that triggers a round early. The timer is
the plan of record; the webhook is added only if time allows.

Each round:

1. Pull farm state via Grafana MCP
2. Pull manifest + next review deadline
3. **Forecast in code** — given observed per-frame rates, which required shots miss? Deterministic arithmetic.
4. **Gemini decides the plan**, only when a miss is forecast: which shot to preempt, what quality to drop to, how to say it to a human. A judgement call with real tradeoffs — a cut shot versus a hero shot; proxy is fine for a review, not for final.
5. Write a Grafana annotation, emit the call sheet

**Principle: the LLM does judgement, code does arithmetic.** This is a maturity
signal to partner judges and it is what keeps the system inside a 250-request
daily quota.

### 5.4 Product surface

Not a Grafana dashboard clone — that is where the Design criterion is lost. A
**shot board**: one card per shot with the last rendered frame as its thumbnail,
progress, ETA against deadline, state colour. Beside it a **call sheet** panel
holding the agent's decision in production language, each claim linking to the
Grafana query that justified it. Plus a live event feed.

FastAPI + server-sent events + a hand-built single page. Explicitly not
Streamlit; this is the surface judges score as a complete, coherent product.

### 5.5 Recursive instrumentation

The agent's own Gemini calls are instrumented with OTel into Grafana Cloud AI
Observability — token cost, latency, MCP tool activity. The agent is watched by
the stack it watches. Nearly free to add, and it scores on Technological
Implementation.

## 6. Evaluation

`bench/` replays a fixed workload — deterministic scene costs, injected failures
— under three schedulers, N runs each:

| Scheduler | Shots delivered before deadline | Deadline misses | Node-hours wasted on cut shots |
|---|---|---|---|
| FIFO | | | |
| Priority-only | | | |
| CALLSHEET | | | |

_(Cells intentionally empty — this is the results template. Numbers are filled
in when the harness runs in the Aug 26–31 window.)_

This is the one thing no other entrant on this track is likely to have. It
carries Technological Implementation and Potential Impact together, and it is
the lesson carried over from Tribunal: a measured result plus a legible demo is
very hard to beat.

## 7. Failure handling

| Failure | Behaviour |
|---|---|
| Grafana MCP unreachable | Agent degrades to last-known state; board shows a "flying blind" banner. Honest degradation is itself a design signal. |
| Gemini quota exhausted | Falls back to the deterministic priority scheduler; banner says so. The demo never dies on stage. |
| Blender crash | Captured as a real log line and becomes agent input. Failures are a feature of this product. |

## 8. Demo — 3 minutes

| Time | Beat |
|---|---|
| 0:00 | Board live, frames appearing |
| 0:20 | A worker is killed, for real |
| 0:35 | Red cascade across the shot board |
| 0:45 | Agent fires; call sheet card appears |
| 1:10 | Grafana MCP call trace, and the annotation landing in Grafana |
| 1:40 | AI Observability panel showing the agent's own cost |
| 2:00 | Ablation table |
| 2:30 | Deadline met |

Rules note: this must be a working demo of the product, not a cinematic trailer.

## 9. Schedule

31 days. Plan of record pulls everything left so September is free for Syzygy
and Inferentia.

| Window | Work |
|---|---|
| Aug 7–12 | Farm + OTel → Grafana Cloud. Riskiest integration, goes first. |
| Aug 13–19 | Domain model, forecaster, scheduler actions, MCP wired into the ADK agent |
| Aug 20–25 | The board. Not compressed — this is where Design is won. |
| Aug 26–31 | Ablation harness + results, Cloud Run deploy, AI Observability |
| Sept 1 | Demo staging, video, README, **submit** |

Slack: Sept 2–7 exists only as buffer. If it is consumed, September's other two
events are the ones that pay for it.

## 10. Compliance checklist

- [ ] Public GitHub repo
- [ ] `LICENSE` file (Apache-2.0) detectable in the About section
- [ ] `google-adk` and `google-genai` imported and called at runtime
- [ ] `mcp-grafana` connection live in code, not just named in the README
- [ ] README with complete run instructions
- [ ] Hosted Cloud Run URL
- [ ] Demo video ≤3 min, public on YouTube, English
- [ ] Devpost submission form, track: **Grafana**
- [ ] No non-Google AI models, agent frameworks, or AI APIs anywhere in the project

## 11. Decisions taken

**Grafana track over Parallel / ClickHouse / IBM / Replit.** Only track whose
partner tier is permanently free with no credit card. Parallel's credits expire
mid-judging; ClickHouse Cloud's trial expires before judging ends; IBM mandates
Bob as the development tool; Replit mandates paid deployment. Grafana is also
the least crowded and the most non-obvious pairing — all three axes agree.

**Render farm over live-streaming delivery or cinema exhibition.** Streaming
observability is the most obvious thing to build on Grafana, so crowding is
high and Quality of Idea suffers. Exhibition has the best legibility but its
telemetry would be entirely invented, which the "genuine understanding of the
problem space" criterion punishes.

**Named CALLSHEET** — the document that tells a crew what is shooting tomorrow.
Domain-native, and it names the output rather than the technology.

**Render-cost variety over famous open-movie assets.** See §5.1.

**Schedule pulled left.** See §9.

---

## 12. Phase 1 findings

Recorded as they land. Phase 2 is planned against these, not against assumptions.

**Render cost is `~4s + k·samples`, not proportional to samples.** Blender's
process startup is a fixed ~4 second floor that dominates cheap shots. Measured
on frame 1, two runs:

| Shot | Samples | Run 1 | Run 2 |
|---|---|---|---|
| SH001 | 16 | 4103 ms | 4418 ms |
| SH002 | 64 | 4701 ms | 7215 ms |
| SH003 | 256 | 23767 ms | 15394 ms |

Three consequences, all of which change Phase 2:

1. **The deadline forecaster must model a fixed per-job overhead**, not a pure
   per-sample rate. A naive linear fit through the origin will badly
   underestimate the cost of many cheap shots and overestimate one expensive one.
2. **Adjacent cheap shots are within noise of each other** — SH001 and SH002
   differ by 0.6s in run 1 and will occasionally invert. Nothing may assert
   strict monotonicity across neighbouring shots.
3. **Run-to-run variance is large** (SH003 swung 15.4s–23.8s, a 1.5x spread on
   identical work). The ablation in §6 therefore needs repeated runs and a
   reported spread, not a single number per scheduler — a single-run table would
   be measuring noise and calling it a result.

This also makes the product's premise more honest, not less: unpredictable
render times are precisely why a coordinator cannot eyeball the queue and know
what will miss.

**OTLP ingest confirmed working.** A point pushed to the ap-south-1 gateway was
queryable 45s later. The riskiest assumption in the whole design is retired.

Resolved facts Phase 2 builds on:

| Fact | Value |
|---|---|
| Stack | `https://vastfoyer1220.grafana.net` (instance `1756233`, region `prod-ap-south-1`) |
| Prometheus datasource UID | `grafanacloud-prom` |
| Traces / logs datasource UIDs | `grafanacloud-traces` / `grafanacloud-logs` |
| Metric names after OTel→Prometheus rewrite | `render_frame_duration_milliseconds_{sum,bucket,count}` |
| Observed ingestion delay | under 45s |

The metric-name rewrite is worth stating plainly because it is the trap the plan
predicted: `render.frame.duration` recorded with `unit="ms"` does **not** arrive
as `render_frame_duration`. Any query written against the OTel name silently
returns nothing rather than erroring.

**The gate passed on the first attempt.** `scripts/spike_end_to_end.py` exited 0
on 2026-08-07: nine real frames rendered, exported over OTLP, and read back
through `mcp-grafana` by code. Total wall clock 224s, of which 90s was the
deliberate ingestion wait and ~131s was rendering — the MCP read-back itself
cost about 3s across three separate stdio sessions.

Per-shot durations for this run, three frames each:

| Shot | Samples | Frame 1 | Frame 2 | Frame 3 | Mean |
|---|---|---|---|---|---|
| SH001 | 16 | 5212 ms | 5475 ms | 4819 ms | 5169 ms |
| SH002 | 64 | 7874 ms | 7366 ms | 7315 ms | 7519 ms |
| SH003 | 256 | 26309 ms | 27229 ms | 26402 ms | 26647 ms |

Four things worth carrying into Phase 2:

1. **The round trip is exact.** Querying
   `sum by (shot) (..._sum) / sum by (shot) (..._count)` back through the MCP
   server returned 5168.57 / 7518.73 / 26646.55 ms against locally measured means
   of 5169 / 7519 / 26647. Nothing was lost, resampled, or rounded in transit,
   and every attribute survived: `shot`, `sequence`, `quality`, `outcome`.
2. **`service.name` becomes the Prometheus `job` label.** The series carry
   `job="callsheet-worker"`. Phase 2 queries must scope on `job`, not on a
   `service_name` label that does not exist.
3. **Within-run variance is far tighter than across-run variance.** Three frames
   of SH003 landed within 3.5% of each other, while §12's earlier two-run table
   showed a 1.5x swing on the same shot. The noise is between process launches,
   not between frames — so the ablation in §6 must repeat whole runs, and
   averaging frames inside one run will understate the true spread.
4. **The gate's own pass condition is weaker than the result it produced.** The
   spike asserts only that `render_frame_duration` appears in the query response;
   a stale series from an earlier run would satisfy it just as well. It passed
   for the right reason here — verified separately by the value match in (1) —
   but Phase 2 should not reuse this check as a regression test without
   pinning it to a fresh timestamp or an expected value.

Ingestion was comfortably inside the 90s budget. The earlier "under 45s"
measurement stands; the conservative wait is kept because a false negative at a
gate costs a full rebuild and 90s costs nothing.

**Gemini 2.5 is gone; the model choice changed.** `gemini-2.5-flash` and
`gemini-2.5-flash-lite` both return `404 — no longer available to new users`.
Verified working on this key: `gemini-3.6-flash` (1.8s for a trivial prompt) and
`gemini-3.5-flash`. **Plan of record is `gemini-3.6-flash`**, free tier ~15 RPM
/ 1500 RPD — more headroom than the 2.5 Flash figures this spec was originally
written against, not less.

**`service.name` becomes the Prometheus `job` label.** Queries scoped on a
`service_name` label return nothing, silently. Use `job="callsheet-worker"`.

**Environment as verified:** Blender 5.2.0 LTS (every 4.x bpy call in the
generator works unchanged), Python 3.12.10, `mcp` 2.0.0, OpenTelemetry SDK
1.44.0, `mcp-grafana` v1.0.0 (73 tools, 8 of them proxied from the connected
Tempo datasource), `gemini-3.6-flash`.

---

_Living doc. Update the same day a decision changes._
