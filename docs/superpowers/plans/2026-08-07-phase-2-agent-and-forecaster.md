# CALLSHEET Phase 2 — Forecaster and Agent Implementation Plan


**Goal:** Turn the proven telemetry spine into a scheduling loop — read real farm state from Grafana, forecast which shots miss the review deadline, let Gemini decide what to sacrifice, and write the decision back as a Grafana annotation.

**Architecture:** A deterministic forecaster does all arithmetic from observed per-shot frame durations read through the Grafana MCP server. Gemini is called *only* when the forecast shows a miss, and only to make the judgement call about which shots to sacrifice. Its structured decision is applied to the job queue and written back to Grafana as an annotation.

**Tech Stack:** Python 3.12.10, `google-genai` (Gemini 3.6 Flash), `google-adk`, `mcp` 2.0, `mcp-grafana` v1.0.0, Grafana Cloud free tier, pytest 8.3.

## Global Constraints

- **$0 total cost, no credit card.** Grafana Cloud free plan; Gemini free tier (~15 RPM / 1500 RPD on `gemini-3.6-flash`).
- **Model is `gemini-3.6-flash`.** `gemini-2.5-flash` and `-flash-lite` return 404, no longer available to new users. Do not use a 2.5 model.
- **No non-Google AI models, agent frameworks, or AI APIs.** Hackathon rule. The only AI dependency permitted anywhere in this project is Google's.
- **The LLM does judgement; code does arithmetic.** Any forecast, duration, or ETA computed by the model instead of by Python is a defect.
- **Python 3.11+. Windows 11 / PowerShell.**
- **No secrets in the repo.** `.env` is gitignored.
- **Tests must pass with no credentials and no network.** Anything needing either is `@pytest.mark.integration`.

## Facts established in Phase 1 — build on these, do not re-derive

| Fact | Value |
|---|---|
| Grafana stack | `https://vastfoyer1220.grafana.net` |
| Prometheus datasource UID | `grafanacloud-prom` |
| Metric names | `render_frame_duration_milliseconds_{sum,bucket,count}` |
| `service.name` surfaces as | Prometheus label `job="callsheet-worker"` |
| Attributes on every point | `shot`, `sequence`, `quality`, `outcome` |
| Key MCP tools | `query_prometheus` (needs `datasourceUid`, `expr`, `endTime`), `query_loki_logs`, `list_datasources`, `create_annotation` |
| Ingestion delay | under 45s |
| Cost model | `duration ≈ startup + k·samples`, startup ≈ 4–5s and **dominates cheap shots** |
| Variance | within-run ±3.5%, across-run up to 1.5x |

---

## File Structure

| File | Responsibility |
|---|---|
| `src/callsheet/domain.py` | `Shot`, `Review`, `FarmState` dataclasses and manifest loading |
| `src/callsheet/forecast.py` | Pure arithmetic: will each required shot finish before the deadline? |
| `src/callsheet/farm_state.py` | Reads observed per-shot frame durations out of Grafana via MCP |
| `src/callsheet/decide.py` | Gemini call: given a forecast miss, choose what to sacrifice |
| `src/callsheet/annotate.py` | Writes the decision back to Grafana as an annotation |
| `src/callsheet/round.py` | Orchestrates one scheduling round |
| `tests/test_domain.py`, `test_forecast.py`, `test_farm_state.py`, `test_decide.py`, `test_round.py` | One test module per unit |

`forecast.py` must import nothing from `decide.py` and must never call the
network. That boundary is what makes the "code does arithmetic" claim auditable
by a judge reading the repo.

---

### Task 1: Domain model

**Files:**
- Create: `src/callsheet/domain.py`
- Test: `tests/test_domain.py`

**Interfaces:**
- Consumes: `scenes/manifest.json` from Phase 1
- Produces: `Shot(id, scene, samples, frames, priority, quality, is_cut)`, `Review(name, deadline_epoch_s, required_shots)`, `FarmState(mean_frame_ms: dict[str, float], frames_done: dict[str, int])`; `load_shots(path) -> list[Shot]`, `load_review(path) -> Review`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_domain.py
import json

from callsheet.domain import Review, Shot, load_review, load_shots


def test_load_shots_reads_the_phase_1_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([
        {"shot": "SH001", "scene": "scenes/SH001.blend", "samples": 16, "frames": [1, 2, 3]},
    ]), encoding="utf-8")

    shots = load_shots(str(path))

    assert len(shots) == 1
    assert shots[0].id == "SH001"
    assert shots[0].frames == [1, 2, 3]
    assert shots[0].samples == 16


def test_shots_default_to_final_quality_not_cut_and_normal_priority(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([
        {"shot": "SH001", "scene": "a.blend", "samples": 16, "frames": [1]},
    ]), encoding="utf-8")

    shot = load_shots(str(path))[0]

    assert shot.quality == "final"
    assert shot.is_cut is False
    assert shot.priority == 50


def test_manifest_may_override_production_fields(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([
        {"shot": "SH002", "scene": "b.blend", "samples": 64, "frames": [1],
         "priority": 90, "is_cut": True, "quality": "proxy"},
    ]), encoding="utf-8")

    shot = load_shots(str(path))[0]

    assert shot.priority == 90
    assert shot.is_cut is True
    assert shot.quality == "proxy"


def test_load_review_reads_deadline_and_required_shots(tmp_path):
    path = tmp_path / "review.json"
    path.write_text(json.dumps({
        "name": "Director review",
        "deadline_epoch_s": 1786050000,
        "required_shots": ["SH001", "SH003"],
    }), encoding="utf-8")

    review = load_review(str(path))

    assert review.name == "Director review"
    assert review.deadline_epoch_s == 1786050000
    assert review.required_shots == ["SH001", "SH003"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.domain'`

- [ ] **Step 3: Write the implementation**

```python
# src/callsheet/domain.py
"""The four production objects. No I/O beyond reading its own JSON files."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

DEFAULT_PRIORITY = 50


@dataclass(frozen=True)
class Shot:
    id: str
    scene: str
    samples: int
    frames: list[int]
    priority: int = DEFAULT_PRIORITY
    quality: str = "final"
    is_cut: bool = False


@dataclass(frozen=True)
class Review:
    name: str
    deadline_epoch_s: int
    required_shots: list[str]


@dataclass(frozen=True)
class FarmState:
    """What Grafana currently knows about the farm."""

    mean_frame_ms: dict[str, float] = field(default_factory=dict)
    frames_done: dict[str, int] = field(default_factory=dict)


def load_shots(path: str) -> list[Shot]:
    with open(path, encoding="utf-8") as handle:
        entries = json.load(handle)
    return [
        Shot(
            id=entry["shot"],
            scene=entry["scene"],
            samples=entry["samples"],
            frames=list(entry["frames"]),
            priority=entry.get("priority", DEFAULT_PRIORITY),
            quality=entry.get("quality", "final"),
            is_cut=entry.get("is_cut", False),
        )
        for entry in entries
    ]


def load_review(path: str) -> Review:
    with open(path, encoding="utf-8") as handle:
        entry = json.load(handle)
    return Review(
        name=entry["name"],
        deadline_epoch_s=int(entry["deadline_epoch_s"]),
        required_shots=list(entry["required_shots"]),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_domain.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/callsheet/domain.py tests/test_domain.py
git commit -m "Add production domain model for shots and reviews"
```

---

### Task 2: Deadline forecaster

**Files:**
- Create: `src/callsheet/forecast.py`
- Test: `tests/test_forecast.py`

**Interfaces:**
- Consumes: `Shot`, `Review`, `FarmState` from Task 1
- Produces: `Forecast(shot_id, frames_remaining, predicted_ms, finishes_at_epoch_s, misses_deadline)`; `forecast_all(shots, review, state, now_epoch_s, fallback_frame_ms=8000.0) -> list[Forecast]`; `misses(forecasts) -> list[Forecast]`

**The whole credibility of the project sits in this file.** It must be pure —
no network, no clock reads, no Gemini. `now_epoch_s` is a parameter precisely so
the tests are deterministic.

**Use observed per-shot mean frame duration directly.** Do not fit a
`startup + k·samples` regression. Phase 1 proved we can read per-shot means
straight out of Grafana, and those means already contain the startup overhead
for that shot. A regression would be more code, more assumptions, and less
accurate than the measurement we already have. Shots with no history fall back
to a global mean.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_forecast.py
from callsheet.domain import FarmState, Review, Shot
from callsheet.forecast import forecast_all, misses

NOW = 1_000_000


def _shot(shot_id, frames=3, priority=50, is_cut=False):
    return Shot(id=shot_id, scene=f"{shot_id}.blend", samples=64,
                frames=list(range(1, frames + 1)), priority=priority, is_cut=is_cut)


def test_uses_observed_mean_for_shots_with_history():
    shots = [_shot("SH001", frames=3)]
    state = FarmState(mean_frame_ms={"SH001": 5000.0}, frames_done={"SH001": 0})
    review = Review("R", NOW + 3600, ["SH001"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.frames_remaining == 3
    assert forecast.predicted_ms == 15000.0
    assert forecast.finishes_at_epoch_s == NOW + 15
    assert forecast.misses_deadline is False


def test_frames_already_done_are_not_re_forecast():
    shots = [_shot("SH001", frames=3)]
    state = FarmState(mean_frame_ms={"SH001": 5000.0}, frames_done={"SH001": 2})
    review = Review("R", NOW + 3600, ["SH001"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.frames_remaining == 1
    assert forecast.predicted_ms == 5000.0


def test_shot_with_no_history_uses_the_fallback():
    shots = [_shot("SH999", frames=2)]
    state = FarmState()
    review = Review("R", NOW + 3600, ["SH999"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW, fallback_frame_ms=8000.0)[0]

    assert forecast.predicted_ms == 16000.0


def test_a_shot_that_cannot_finish_in_time_is_flagged():
    shots = [_shot("SH003", frames=3)]
    state = FarmState(mean_frame_ms={"SH003": 26000.0}, frames_done={"SH003": 0})
    review = Review("R", NOW + 60, ["SH003"])   # 78s of work, 60s of runway

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.misses_deadline is True
    assert misses([forecast]) == [forecast]


def test_shots_are_forecast_sequentially_because_the_farm_is_one_queue():
    """Two shots share the farm, so the second starts after the first finishes."""
    shots = [_shot("SH001", frames=1), _shot("SH002", frames=1)]
    state = FarmState(mean_frame_ms={"SH001": 5000.0, "SH002": 7000.0}, frames_done={})
    review = Review("R", NOW + 3600, ["SH001", "SH002"])

    first, second = forecast_all(shots, review, state, now_epoch_s=NOW)

    assert first.finishes_at_epoch_s == NOW + 5
    assert second.finishes_at_epoch_s == NOW + 12


def test_shots_not_required_by_the_review_are_still_forecast_but_never_miss():
    shots = [_shot("SH004", frames=1)]
    state = FarmState(mean_frame_ms={"SH004": 99_000.0}, frames_done={})
    review = Review("R", NOW + 1, [])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.misses_deadline is False, "only required shots can miss a review"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_forecast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.forecast'`

- [ ] **Step 3: Write the implementation**

```python
# src/callsheet/forecast.py
"""Deterministic deadline arithmetic. No network, no clock, no model."""

from __future__ import annotations

from dataclasses import dataclass

from callsheet.domain import FarmState, Review, Shot


@dataclass(frozen=True)
class Forecast:
    shot_id: str
    frames_remaining: int
    predicted_ms: float
    finishes_at_epoch_s: int
    misses_deadline: bool


def forecast_all(
    shots: list[Shot],
    review: Review,
    state: FarmState,
    now_epoch_s: int,
    fallback_frame_ms: float = 8000.0,
) -> list[Forecast]:
    """Forecast completion for every shot, in queue order.

    The farm is a single queue, so each shot starts when the previous finishes.
    Per-shot mean frame duration is read from observed telemetry and already
    includes Blender's fixed startup cost for that shot, so no separate
    overhead term is modelled.
    """
    forecasts: list[Forecast] = []
    cursor_ms = 0.0

    for shot in shots:
        done = state.frames_done.get(shot.id, 0)
        remaining = max(0, len(shot.frames) - done)
        per_frame = state.mean_frame_ms.get(shot.id, fallback_frame_ms)
        predicted = remaining * per_frame

        cursor_ms += predicted
        finishes_at = now_epoch_s + int(cursor_ms // 1000)

        required = shot.id in review.required_shots
        forecasts.append(
            Forecast(
                shot_id=shot.id,
                frames_remaining=remaining,
                predicted_ms=predicted,
                finishes_at_epoch_s=finishes_at,
                misses_deadline=required and finishes_at > review.deadline_epoch_s,
            )
        )

    return forecasts


def misses(forecasts: list[Forecast]) -> list[Forecast]:
    return [forecast for forecast in forecasts if forecast.misses_deadline]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_forecast.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/callsheet/forecast.py tests/test_forecast.py
git commit -m "Add deterministic deadline forecaster driven by observed frame times"
```

---

### Task 3: Farm state from Grafana

**Files:**
- Create: `src/callsheet/farm_state.py`
- Test: `tests/test_farm_state.py`

**Interfaces:**
- Consumes: `call_tool` from `callsheet.grafana_mcp` (Phase 1), `FarmState` from Task 1
- Produces: `parse_farm_state(mean_json: str) -> FarmState` (pure, unit-tested) and `async read_farm_state(config) -> FarmState` (integration-tested)

Split deliberately: the parsing is pure and testable offline against captured
fixtures; only the fetching needs credentials.

> **Grafana supplies the rate, never the remaining work.** An earlier draft of
> this task also read `frames_done` from
> `sum by (shot) (render_frame_duration_milliseconds_count)`. That is a *lifetime*
> counter — "frames ever rendered for this shot" — not "frames done toward this
> review. Since Phase 1 already rendered 3 frames of each shot and the manifest
> declares 3 frames each, every shot would forecast `remaining = 0`, nothing
> would ever miss, and the Phase 2 demo could never fire. Re-running the spike
> would make it worse, not better.
>
> The underlying error was conflating two questions. **Telemetry answers "how
> fast is this shot?"** — and there, more history is strictly better. **The queue
> answers "what is left?"** Phase 2 therefore reads only the rate; `frames_done`
> is supplied by the caller and is empty until Plan 3 builds the job queue. That
> is also why `FarmState.frames_done` keeps its default of `{}`.

One PromQL query, using the names Phase 1 established:

```
sum by (shot) (rate(render_frame_duration_milliseconds_sum[1h]))
  / sum by (shot) (rate(render_frame_duration_milliseconds_count[1h]))
```

A `[1h]` window rather than `[10m]`: SH003 alone takes 80 seconds, and a
10-minute window ages out between a render and a later demo run, silently
returning an empty result that looks identical to a cold farm.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_farm_state.py
import json
import os

import pytest

from callsheet.farm_state import parse_farm_state

MEANS = json.dumps({"data": [
    {"metric": {"shot": "SH001"}, "value": [1786047271.417, "5168.57"]},
    {"metric": {"shot": "SH003"}, "value": [1786047271.417, "26646.55"]},
]})


def test_parses_mean_frame_duration_per_shot():
    state = parse_farm_state(MEANS)
    assert state.mean_frame_ms["SH001"] == pytest.approx(5168.57)
    assert state.mean_frame_ms["SH003"] == pytest.approx(26646.55)


def test_frames_done_is_never_read_from_telemetry():
    """Grafana knows the rate. Only the queue knows what is left to do."""
    state = parse_farm_state(MEANS)
    assert state.frames_done == {}


def test_empty_response_yields_empty_state_not_an_error():
    """A cold farm has no series yet. That is normal, not a failure."""
    state = parse_farm_state(json.dumps({"data": []}))
    assert state.mean_frame_ms == {}


def test_series_without_a_shot_label_is_ignored():
    noisy = json.dumps({"data": [{"metric": {}, "value": [0, "123"]}]})
    assert parse_farm_state(noisy).mean_frame_ms == {}


def test_nan_mean_is_dropped_rather_than_poisoning_the_forecast():
    """rate() over a window with one sample yields NaN. It must not become 0.0."""
    nan_means = json.dumps({"data": [{"metric": {"shot": "SH001"}, "value": [0, "NaN"]}]})
    assert "SH001" not in parse_farm_state(nan_means).mean_frame_ms


def test_prometheus_http_api_shape_is_also_accepted():
    """mcp-grafana may hand back {"data": {"result": [...]}} rather than {"data": [...]}."""
    wrapped = json.dumps({"data": {"result": [
        {"metric": {"shot": "SH001"}, "value": [0, "1234.5"]},
    ]}})
    assert parse_farm_state(wrapped).mean_frame_ms["SH001"] == pytest.approx(1234.5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reads_real_farm_state_from_grafana():
    from callsheet.config import Config
    from callsheet.farm_state import read_farm_state

    state = await read_farm_state(Config.from_env(os.environ))
    assert state.mean_frame_ms, "expected the Phase 1 render to still be visible"
    for shot, mean in state.mean_frame_ms.items():
        assert mean > 0, f"{shot} has a non-positive mean"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_farm_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.farm_state'`

- [ ] **Step 3: Write the implementation**

```python
# src/callsheet/farm_state.py
"""Reads observed farm state out of Grafana through the MCP server."""

from __future__ import annotations

import json
import math

from callsheet.config import Config
from callsheet.domain import FarmState
from callsheet.grafana_mcp import call_tool

PROM_DATASOURCE_UID = "grafanacloud-prom"

MEAN_QUERY = (
    'sum by (shot) (rate(render_frame_duration_milliseconds_sum[1h]))'
    ' / sum by (shot) (rate(render_frame_duration_milliseconds_count[1h]))'
)


def _series(raw: str) -> list[dict]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    data = payload.get("data", [])
    if isinstance(data, dict):          # Prometheus HTTP API shape
        data = data.get("result", [])
    return data if isinstance(data, list) else []


def _by_shot(raw: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for entry in _series(raw):
        shot = entry.get("metric", {}).get("shot")
        if not shot:
            continue
        try:
            value = float(entry["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if math.isnan(value) or math.isinf(value):
            continue
        values[shot] = value
    return values


def parse_farm_state(mean_json: str) -> FarmState:
    """Observed per-shot frame rate. Deliberately does not report progress.

    Telemetry answers "how fast is this shot?", where a longer history is
    strictly better. It cannot answer "what is left?" — the metrics counter is
    cumulative over the shot's lifetime, not over the current review. That
    question belongs to the job queue, which arrives in Plan 3.
    """
    return FarmState(mean_frame_ms=_by_shot(mean_json))


async def read_farm_state(config: Config) -> FarmState:
    mean_raw = await call_tool(config, "query_prometheus", {
        "datasourceUid": PROM_DATASOURCE_UID,
        "expr": MEAN_QUERY,
        "queryType": "instant",
        "endTime": "now",
    })
    return parse_farm_state(mean_raw)
```

- [ ] **Step 4: Run both test tiers**

Run: `python -m pytest tests/test_farm_state.py -v`
Expected: 5 passed, 1 deselected

Run: `python -m pytest tests/test_farm_state.py -v -m integration`
Expected: 1 passed. **If the means come back empty**, the `rate(...[10m])` window
has aged past the Phase 1 render — widen to `[1h]` or re-run the spike to
generate fresh data, and say which you did.

- [ ] **Step 5: Commit**

```bash
git add src/callsheet/farm_state.py tests/test_farm_state.py
git commit -m "Read observed per-shot frame durations from Grafana"
```

---

### Task 4: The Gemini decision

**Files:**
- Create: `src/callsheet/decide.py`
- Test: `tests/test_decide.py`

**Interfaces:**
- Consumes: `Shot`, `Review` (Task 1), `Forecast` (Task 2)
- Produces: `Decision(actions: list[Action], summary: str)`, `Action(shot_id, action, reason)` where `action ∈ {"preempt", "downgrade", "escalate"}`; `build_prompt(shots, review, forecasts) -> str` (pure) and `decide(config, shots, review, forecasts) -> Decision`

**This is the only place a model is called.** It receives an already-computed
forecast and returns a judgement. It must never be asked to compute a duration,
an ETA, or whether something misses — those arrive as facts in the prompt.

- [ ] **Step 1: Add the dependency**

Add to `pyproject.toml` dependencies: `"google-genai>=1.0"`. Then
`pip install -e ".[dev]"`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_decide.py
import os

import pytest

from callsheet.domain import Review, Shot
from callsheet.forecast import Forecast
from callsheet.decide import build_prompt, parse_decision

NOW = 1_000_000

SHOTS = [
    Shot("SH001", "a.blend", 16, [1, 2, 3], priority=90, quality="final", is_cut=False),
    Shot("SH002", "b.blend", 64, [1, 2, 3], priority=10, quality="final", is_cut=True),
]
REVIEW = Review("Director review", NOW + 60, ["SH001"])
FORECASTS = [
    Forecast("SH001", 3, 90_000.0, NOW + 90, True),
    Forecast("SH002", 3, 21_000.0, NOW + 111, False),
]


def test_prompt_states_the_deadline_and_the_missing_shot():
    prompt = build_prompt(SHOTS, REVIEW, FORECASTS)
    assert "SH001" in prompt
    assert "Director review" in prompt
    assert "30" in prompt, "the 30s shortfall must be stated, not left to be computed"


def test_prompt_marks_cut_shots_so_the_model_can_prefer_sacrificing_them():
    prompt = build_prompt(SHOTS, REVIEW, FORECASTS)
    assert "SH002" in prompt
    assert "cut" in prompt.lower()


def test_nothing_sent_to_the_model_asks_it_to_do_arithmetic():
    """Covers the system instruction too, not just the data block.

    An earlier version asserted only on build_prompt, which is the half that was
    never going to contain a calculation request anyway.
    """
    from callsheet.decide import SYSTEM

    everything = (SYSTEM + build_prompt(SHOTS, REVIEW, FORECASTS)).lower()
    for banned in ("calculate", "compute", "work out", "estimate how long"):
        assert banned not in everything, f"{banned!r} reaches the model"


def test_parse_decision_reads_structured_actions():
    decision = parse_decision(
        '{"summary": "Preempting the cut shot.",'
        ' "actions": [{"shot_id": "SH002", "action": "preempt", "reason": "already cut"}]}'
    )
    assert decision.summary == "Preempting the cut shot."
    assert decision.actions[0].shot_id == "SH002"
    assert decision.actions[0].action == "preempt"


def test_parse_decision_rejects_an_unknown_action():
    with pytest.raises(ValueError, match="unknown action"):
        parse_decision('{"summary": "x", "actions": [{"shot_id": "SH002", '
                       '"action": "delete_everything", "reason": "y"}]}')


def test_parse_decision_survives_a_fenced_code_block():
    """Models wrap JSON in ``` fences often enough that this must not be fatal."""
    decision = parse_decision('```json\n{"summary": "s", "actions": []}\n```')
    assert decision.summary == "s"


@pytest.mark.integration
def test_gemini_returns_a_usable_decision():
    from callsheet.config import Config
    from callsheet.decide import decide

    decision = decide(Config.from_env(os.environ), SHOTS, REVIEW, FORECASTS)

    assert decision.summary
    assert decision.actions, "a forecast miss should produce at least one action"
    assert all(a.action in {"preempt", "downgrade", "escalate"} for a in decision.actions)
    # The sensible call is to sacrifice the cut shot, not the hero shot.
    assert any(a.shot_id == "SH002" for a in decision.actions)
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_decide.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.decide'`

- [ ] **Step 4: Write the implementation**

First extend `Config`. **Field order matters:** `mcp_grafana_path` carries a
default, so a new field without one cannot follow it — a dataclass raises
`TypeError: non-default argument follows default argument` at import time. Put
`gemini_api_key` *before* it.

```python
# src/callsheet/config.py — add to _FIELDS
    "gemini_api_key": "GEMINI_API_KEY",

# and in the Config dataclass, ABOVE mcp_grafana_path:
    gemini_api_key: str
    mcp_grafana_path: str = "mcp-grafana"
```

Add to `.env.example`:

```dotenv
# Google AI Studio — https://aistudio.google.com > Get API key
GEMINI_API_KEY=
```

And extend `tests/test_config.py`:

```python
def test_gemini_api_key_is_required():
    with pytest.raises(ConfigError) as excinfo:
        Config.from_env({})
    assert "GEMINI_API_KEY" in str(excinfo.value)
```

`FULL_ENV` in that module must gain `"GEMINI_API_KEY": "AIza_test"` or every
existing config test breaks.

```python
# src/callsheet/decide.py
"""The single place a language model is called.

It receives a completed forecast and makes the production judgement call.
It is never asked to compute a duration, an ETA, or whether a shot misses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from google import genai
from google.genai import types

from callsheet.config import Config
from callsheet.domain import Review, Shot
from callsheet.forecast import Forecast

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
        lines.append(
            f"{shot.id}: {forecast.frames_remaining} frames left, "
            f"priority {shot.priority}, quality {shot.quality}, "
            f"{'CUT by the director' if shot.is_cut else 'in the edit'} — {status}"
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
```

- [ ] **Step 5: Run both test tiers**

Run: `python -m pytest tests/test_decide.py -v`
Expected: 6 passed, 1 deselected

Run: `python -m pytest tests/test_decide.py -v -m integration -s`
Expected: 1 passed. **Verify the `google-genai` API shape first** — confirm
`genai.Client(api_key=...)`, `client.models.generate_content(...)` and
`response.text` are current. If the SDK moved, adapt and report exactly what.

- [ ] **Step 6: Commit**

```bash
git add src/callsheet/decide.py tests/test_decide.py src/callsheet/config.py tests/test_config.py .env.example pyproject.toml
git commit -m "Add the Gemini production decision, the only model call in the system"
```

---

### Task 5: Annotation write-back

**Files:**
- Create: `src/callsheet/annotate.py`
- Test: `tests/test_annotate.py`

**Interfaces:**
- Consumes: `Decision` (Task 4), `call_tool` (Phase 1)
- Produces: `build_annotation(decision, now_epoch_s) -> dict` (pure); `async write_annotation(config, decision, now_epoch_s) -> str`

This closes the loop: the agent does not merely read Grafana, it writes back to
it. That round trip is the strongest possible evidence of the partner
integration being load-bearing rather than decorative.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_annotate.py
import os

import pytest

from callsheet.annotate import build_annotation
from callsheet.decide import Action, Decision

DECISION = Decision(
    summary="Preempting SH002 so SH001 makes the 09:00 review.",
    actions=[Action("SH002", "preempt", "already cut")],
)


def test_annotation_carries_the_summary_as_text():
    payload = build_annotation(DECISION, now_epoch_s=1_000_000)
    assert "SH002" in payload["text"]
    assert DECISION.summary in payload["text"]


def test_annotation_time_is_epoch_milliseconds():
    """Grafana expects ms. Passing seconds silently places it in 1970."""
    payload = build_annotation(DECISION, now_epoch_s=1_000_000)
    assert payload["time"] == 1_000_000_000


def test_annotation_is_tagged_for_retrieval():
    payload = build_annotation(DECISION, now_epoch_s=1_000_000)
    assert "callsheet" in payload["tags"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_writes_a_real_annotation_and_reads_it_back():
    """Uses the real clock deliberately.

    A fixed epoch of 1_000_000 dates the annotation to January 1970, where
    Grafana's default time range will never show it — a successful write would
    look like a failure. Determinism belongs in the pure tests above; this one
    needs to land somewhere a human can actually see it.
    """
    import time

    from callsheet.annotate import write_annotation
    from callsheet.config import Config
    from callsheet.grafana_mcp import call_tool

    config = Config.from_env(os.environ)
    now = int(time.time())

    result = await write_annotation(config, DECISION, now_epoch_s=now)
    assert "error" not in result.lower(), result

    # Read it back by tag. `create_annotation` can accept and discard a
    # malformed payload, so a non-error response is not evidence of anything.
    found = await call_tool(config, "get_annotations", {"tags": ["callsheet"], "matchAny": False})
    assert DECISION.summary in found, f"annotation not retrievable: {found[:400]}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_annotate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.annotate'`

- [ ] **Step 3: Write the implementation**

```python
# src/callsheet/annotate.py
"""Writes the agent's decision back into Grafana as an annotation."""

from __future__ import annotations

from callsheet.config import Config
from callsheet.decide import Decision
from callsheet.grafana_mcp import call_tool


def build_annotation(decision: Decision, now_epoch_s: int) -> dict:
    detail = "; ".join(
        f"{action.action} {action.shot_id} ({action.reason})" for action in decision.actions
    )
    return {
        "text": f"CALLSHEET: {decision.summary} — {detail}" if detail else f"CALLSHEET: {decision.summary}",
        "time": now_epoch_s * 1000,
        "tags": ["callsheet", "scheduling-decision"],
    }


async def write_annotation(config: Config, decision: Decision, now_epoch_s: int) -> str:
    return await call_tool(config, "create_annotation", build_annotation(decision, now_epoch_s))
```

- [ ] **Step 4: Run both tiers**

Run: `python -m pytest tests/test_annotate.py -v`
Expected: 3 passed, 1 deselected

Run: `python -m pytest tests/test_annotate.py -v -m integration -s`
Expected: 1 passed. Then **confirm it visually** in Grafana under Dashboards →
Annotations, or by querying annotations back. `create_annotation` declares no
required arguments in its schema, so a silently-rejected payload is a real
possibility — do not trust a non-error response alone.

- [ ] **Step 5: Commit**

```bash
git add src/callsheet/annotate.py tests/test_annotate.py
git commit -m "Write scheduling decisions back to Grafana as annotations"
```

---

### Task 6: The scheduling round

**Files:**
- Create: `src/callsheet/round.py`
- Test: `tests/test_round.py`

**Interfaces:**
- Consumes: everything above
- Produces: `RoundResult(forecasts, decision, annotation_written)`; `async run_round(config, shots, review, now_epoch_s) -> RoundResult`

**Gemini is called only when the forecast shows a miss.** On a healthy farm a
round costs zero model calls. This is what keeps the system inside the free tier
and it must be enforced by a test, not by good intentions.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_round.py
from unittest.mock import AsyncMock, patch

import pytest

from callsheet.config import Config
from callsheet.decide import Action, Decision
from callsheet.domain import FarmState, Review, Shot
from callsheet.round import run_round

NOW = 1_000_000
# Keyword args deliberately: Config's field order changed in Task 4, and
# positional construction here would bind gemini_api_key to mcp_grafana_path.
CONFIG = Config(
    grafana_url="https://x.grafana.net",
    grafana_token="glsa_abc",
    otlp_endpoint="https://o/otlp",
    otlp_auth="aGVsbG8=",
    blender_path="blender.exe",
    gemini_api_key="AIza_test",
    mcp_grafana_path="mcp-grafana",
)
SHOTS = [Shot("SH001", "a.blend", 16, [1, 2, 3], priority=90)]


@pytest.mark.asyncio
async def test_healthy_farm_makes_no_model_call():
    """The free tier survives only if a quiet round costs nothing."""
    state = FarmState(mean_frame_ms={"SH001": 1000.0}, frames_done={})
    review = Review("R", NOW + 3600, ["SH001"])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide") as decide_mock, \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    decide_mock.assert_not_called()
    assert result.decision is None
    assert result.annotation_written is False


@pytest.mark.asyncio
async def test_a_forecast_miss_triggers_a_decision_and_an_annotation():
    state = FarmState(mean_frame_ms={"SH001": 60_000.0}, frames_done={})
    review = Review("R", NOW + 10, ["SH001"])
    decision = Decision("late", [Action("SH001", "downgrade", "will miss")])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", return_value=decision) as decide_mock, \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")) as write_mock:
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    decide_mock.assert_called_once()
    write_mock.assert_awaited_once()
    assert result.decision is decision
    assert result.annotation_written is True


@pytest.mark.asyncio
async def test_a_model_failure_does_not_abort_the_round():
    """Quota exhaustion must degrade, not crash — the demo has to survive it."""
    state = FarmState(mean_frame_ms={"SH001": 60_000.0}, frames_done={})
    review = Review("R", NOW + 10, ["SH001"])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", side_effect=RuntimeError("429 quota exhausted")), \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    assert result.decision is None
    assert result.degraded_reason is not None
    assert "quota" in result.degraded_reason.lower()
    assert result.forecasts, "forecasts are still valid without the model"


@pytest.mark.asyncio
async def test_an_annotation_failure_does_not_discard_the_decision():
    """A Grafana blip must not throw away work that already succeeded."""
    state = FarmState(mean_frame_ms={"SH001": 60_000.0}, frames_done={})
    review = Review("R", NOW + 10, ["SH001"])
    decision = Decision("late", [Action("SH001", "downgrade", "will miss")])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", return_value=decision), \
         patch("callsheet.round.write_annotation", AsyncMock(side_effect=RuntimeError("grafana 503"))):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    assert result.decision is decision, "the decision survives a failed write"
    assert result.annotation_written is False
    assert "503" in result.degraded_reason
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_round.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.round'`

- [ ] **Step 3: Write the implementation**

```python
# src/callsheet/round.py
"""One scheduling round: observe, forecast, and only then judge."""

from __future__ import annotations

from dataclasses import dataclass

from callsheet.annotate import write_annotation
from callsheet.config import Config
from callsheet.decide import Decision, decide
from callsheet.domain import Review, Shot
from callsheet.farm_state import read_farm_state
from callsheet.forecast import Forecast, forecast_all, misses


@dataclass(frozen=True)
class RoundResult:
    forecasts: list[Forecast]
    decision: Decision | None
    annotation_written: bool
    degraded_reason: str | None = None


async def run_round(config: Config, shots: list[Shot], review: Review,
                    now_epoch_s: int) -> RoundResult:
    state = await read_farm_state(config)
    forecasts = forecast_all(shots, review, state, now_epoch_s)

    if not misses(forecasts):
        return RoundResult(forecasts, None, False)

    try:
        decision = decide(config, shots, review, forecasts)
    except Exception as error:      # noqa: BLE001 — degrade, never crash the loop
        return RoundResult(forecasts, None, False, degraded_reason=str(error))

    # The write is wrapped too. A Grafana blip must not discard a forecast and a
    # decision that both succeeded — that is the same failure class the model
    # call is already protected against.
    try:
        await write_annotation(config, decision, now_epoch_s)
    except Exception as error:      # noqa: BLE001
        return RoundResult(forecasts, decision, False, degraded_reason=f"annotation failed: {error}")

    return RoundResult(forecasts, decision, True)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_round.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -v`
Expected: everything green, integration deselected.

- [ ] **Step 6: Commit**

```bash
git add src/callsheet/round.py tests/test_round.py
git commit -m "Add the scheduling round, calling the model only on a forecast miss"
```

---

### Task 7: End-to-end Phase 2 demonstration

**Files:**
- Create: `scripts/demo_round.py`, `review.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything
- Produces: exit code 0, and a visible annotation in Grafana

- [ ] **Step 1: Create `review.json` with a deliberately tight deadline**

```json
{
  "name": "Director review",
  "deadline_epoch_s": 0,
  "required_shots": ["SH001", "SH003"]
}
```

`deadline_epoch_s: 0` is a sentinel — the script replaces it with
`now + DEADLINE_SECONDS` so the demo is reproducible on any day.

- [ ] **Step 2: Write the demo script**

```python
# scripts/demo_round.py
"""Phase 2 gate: observe the real farm, forecast a miss, let Gemini decide, annotate.

Run:
    python scripts/demo_round.py
Exit 0 means the full loop worked against live Grafana and live Gemini.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

from callsheet.config import Config, ConfigError
from callsheet.domain import Review, load_shots
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
    review = Review("Director review", now + DEADLINE_SECONDS, ["SH001", "SH003"])

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
```

- [ ] **Step 3: Run it**

Run: `python scripts/demo_round.py`
Expected: exit 0, at least one `MISS`, a call sheet, and `annotation written: True`.

**Exit 3 means the deadline was not tight enough to force a miss** — lower
`DEADLINE_SECONDS` and retry. It does *not* mean the farm lacks history; since
`frames_done` is empty in Phase 2, every shot is forecast at its full frame
count, so a sufficiently tight deadline always produces a miss.

If the forecast shows every shot at the 8000 ms fallback rather than the ~5.2s /
7.5s / 26.6s observed in Phase 1, the `[1h]` rate window has aged past that
render. Re-run `python scripts/spike_end_to_end.py` to generate fresh telemetry.

- [ ] **Step 4: Confirm the annotation landed in Grafana**

Task 5's integration test already reads it back by tag through `get_annotations`,
which is the real assertion. Open the stack → **Dashboards → Annotations**,
filter tag `callsheet`, as a secondary visual confirmation — but the mechanical
read-back is what counts.

- [ ] **Step 5: Update the README** with a Phase 2 section and the new run command.

- [ ] **Step 6: Commit**

```bash
git add scripts/demo_round.py review.json README.md
git commit -m "Add the Phase 2 demonstration of the full scheduling round"
```

---

## What Phase 2 deliberately does not build

No shot board, no ADK multi-agent structure, no ablation, no deploy. Plan 3 is
the board, which is where the Design criterion is won.

**Two known gaps, stated rather than discovered later:**

1. **Decisions are made and annotated, but not applied.** The spec's §5.3 names
   `requeue(shot, quality)` and `preempt(shot)` as production tools, and Phase 2
   builds neither — because there is nothing to apply them to. §5.1's SQLite job
   queue does not exist yet; Phase 1 rendered a manifest linearly. The queue and
   the board are the same subsystem (the board's data source *is* the queue), so
   both land in Plan 3, and the actions become real there. Until then the loop
   is observe → forecast → decide → record, which is honest but not yet acting.
2. **Still one metric.** `render.frame.memory`, `queue.depth` and `worker.busy`,
   plus the Loki log path and per-frame spans, were deferred from Phase 1 and are
   deferred again. Logs matter most — a Blender crash should become agent input
   per §7. That lands in Plan 3 alongside the queue, since a queue is what makes
   `queue.depth` meaningful in the first place.

**On `google-adk`:** the spec names it, and the hackathon accepts
`google-adk`, `google-genai`, `google-generativeai` or `google-cloud-aiplatform`
equally. Phase 2 uses `google-genai` directly because a single structured
decision call does not need an agent framework, and wrapping it in one would add
ceremony a judge can see through. **If Plan 3 or 4 introduces genuine multi-step
tool use, migrate to `google-adk` then** — when it earns its place.

## Definition of done for Phase 2

- [ ] `python -m pytest` green with no credentials
- [ ] `python -m pytest -m integration` green with credentials
- [ ] `python scripts/demo_round.py` exits 0
- [ ] An annotation tagged `callsheet` is visible in Grafana
- [ ] A healthy farm provably makes zero Gemini calls
- [ ] Phase 2 findings appended to the design doc
