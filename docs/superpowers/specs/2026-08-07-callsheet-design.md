# CALLSHEET — design

**Date:** 2026-08-07
**Event:** Google Cloud Agentic Cinema Hackathon (Devpost), Grafana partner track
**Deadline:** 2026-09-07, 14:00 PT — 31 days
**Builder:** solo
**Status:** design approved, not yet implemented

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
| Gemini free-tier quota | 2.5 Flash: 10 RPM / 250 RPD (quotas cut 50–80% in Dec 2025). Architecture keeps the LLM to a handful of calls per scheduling round, not one per metric. |
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
Blender workers ──OTel──► Grafana Cloud ◄──MCP── ADK agent (Gemini 2.5 Flash)
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

Google ADK agent on Gemini 2.5 Flash via `google-genai`. Two tool families:

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

_Living doc. Update the same day a decision changes._
