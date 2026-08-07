# CALLSHEET Phase 3 — Job Queue and Decision Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent's decisions real and honest — apply them to an actual job queue, then re-forecast to prove whether they close the deadline gap, and escalate when nothing can.

**Architecture:** A SQLite job queue becomes the source of truth for what work remains, replacing the empty `frames_done` Phase 2 carried. Gemini's actions are applied to a *copy* of that queue and the forecast is re-run, so the system can state a residual shortfall rather than assert success. A structural guard rejects actions that cannot help before they are ever applied.

**Tech Stack:** Python 3.12.10, SQLite (stdlib `sqlite3`), plus everything from Phases 1–2.

## Global Constraints

- **$0, no credit card.** No new services. SQLite is stdlib.
- **Model is `gemini-3.6-flash`.** 2.5 models 404 for new users.
- **No non-Google AI models, agent frameworks, or AI APIs.**
- **The LLM does judgement; code does arithmetic.** The residual check is arithmetic and must never be delegated to the model.
- **Python 3.11+. Windows 11 / PowerShell.**
- **Tests must pass with no credentials and no network**, except `@pytest.mark.integration`.
- Current baseline: **57 passed, 6 deselected.** Keep it green.

## Why this phase exists

Phase 2 ends with the system printing `PASS` after a decision that does not
work. Preempting SH002 returns 22.0s to SH003, which needs roughly 66s more than
it has. The decision is directionally right, no longer inert — and insufficient.
The annotation therefore records *intent*, not *outcome*.

A coordinator who says "fixed" when they have not is worse than one who says
"this cannot be saved, wake someone." Building the honest version is also the
more defensible thing to demonstrate to a judge, because the failure case is
where an agentic system's design is actually visible.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/callsheet/queue.py` | SQLite job queue: enqueue, claim, complete, snapshot |
| `src/callsheet/apply.py` | Applies decision actions to a queue snapshot; pure |
| `src/callsheet/verify.py` | Re-forecasts post-action and reports residual shortfall; pure |
| `src/callsheet/guard.py` | Rejects actions that cannot help, before they are applied; pure |
| `tests/test_queue.py`, `test_apply.py`, `test_verify.py`, `test_guard.py` | One per unit |

`apply.py`, `verify.py` and `guard.py` are pure and take snapshots, never a live
connection. That keeps the arithmetic auditable and the tests instant.

---

### Task 1: Measure the proxy quality tier

**Files:**
- Modify: `scenes/make_scenes.py`, `scripts/spike_end_to_end.py`
- Test: `tests/test_scenes.py`

**Interfaces:**
- Produces: telemetry carrying both `quality="final"` and `quality="proxy"` for every shot

**A downgrade's benefit must be measured, not assumed.** Phase 2 can propose
`downgrade` but has no idea what it buys, and inventing a "proxy is 40% faster"
constant is exactly the kind of unfounded number a judge should punish. The
telemetry already carries a `quality` label — nothing is emitting `proxy` into
it yet.

Proxy = the same scene at one quarter the samples, set through Blender's
`--python-expr` (there is no CLI flag for sample count; this is why Phase 1
dropped the dead `samples` parameter).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scenes.py — add
import os
import pytest


@pytest.mark.integration
def test_proxy_renders_measurably_faster_than_final():
    """A downgrade must buy real time, and we must know how much."""
    import json
    from callsheet.render import render_frame

    blender = os.environ.get("BLENDER_PATH")
    if not blender:
        pytest.skip("BLENDER_PATH is not set")

    with open("scenes/manifest.json", encoding="utf-8") as handle:
        entry = json.load(handle)[-1]          # SH003, the expensive one

    final = render_frame(blender, entry["scene"], entry["shot"], 1, "out")
    proxy = render_frame(blender, entry["scene"], entry["shot"], 1, "out",
                         samples_override=entry["samples"] // 4)

    assert proxy.succeeded and final.succeeded
    assert proxy.duration_ms < final.duration_ms, (
        f"proxy {proxy.duration_ms:.0f}ms was not faster than final {final.duration_ms:.0f}ms"
    )
    print(f"\nSH003 final={final.duration_ms:.0f}ms proxy={proxy.duration_ms:.0f}ms "
          f"speedup={final.duration_ms / proxy.duration_ms:.2f}x")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_scenes.py -m integration -v`
Expected: FAIL — `render_frame() got an unexpected keyword argument 'samples_override'`

- [ ] **Step 3: Add the override to `render_frame`**

```python
# src/callsheet/render.py — replace the signature and command construction
def render_frame(
    blender_path: str,
    scene: str,
    shot: str,
    frame: int,
    out_dir: str,
    timeout_s: int = 600,
    samples_override: int | None = None,
) -> RenderResult:
    """Render one frame in Blender's background mode.

    Sample count is normally baked into the .blend by scenes/make_scenes.py.
    `samples_override` lowers it for proxy-quality renders; Blender has no CLI
    flag for this, so it goes through --python-expr.
    """
    command = [blender_path, "-b", scene]
    if samples_override is not None:
        command += ["--python-expr",
                    f"import bpy; bpy.context.scene.cycles.samples = {samples_override}"]
    command += ["-o", f"{out_dir}/{shot}_", "-F", "PNG", "-f", str(frame)]
```

The rest of the function is unchanged. **Order matters:** `--python-expr` must
come after the `.blend` and before `-f`, or Blender runs the expression against
the wrong scene or after the render has already been queued.

- [ ] **Step 4: Verify, and record the measured speedup**

Run: `python -m pytest tests/test_scenes.py -m integration -v -s`
Expected: PASS, with the speedup printed. **Record that number in the report** —
it is the factor the forecaster will rely on.

Also add a unit test that the flag is only present when asked:

```python
def test_no_python_expr_when_samples_are_not_overridden():
    with patch("subprocess.run", return_value=_completed(0)) as run:
        render_frame("blender.exe", "a.blend", "SH001", 1, "out")
    assert "--python-expr" not in run.call_args[0][0]
```

- [ ] **Step 5: Emit proxy telemetry**

Extend `scripts/spike_end_to_end.py` to render the manifest twice — once at
`quality="proxy"` with `samples_override=samples // 4`, once at `"final"` — so
Grafana holds a measured rate for both tiers. `run_manifest` already takes a
`quality` argument and passes it to `record_render`; thread `samples_override`
through alongside it.

- [ ] **Step 6: Run the spike and confirm both tiers land**

Run: `python scripts/spike_end_to_end.py`
Then confirm in Grafana that `sum by (shot, quality) (...)` returns six series,
not three.

- [ ] **Step 7: Commit**

```bash
git add src/callsheet/render.py scripts/spike_end_to_end.py src/callsheet/worker.py tests/test_scenes.py tests/test_render.py
git commit -m "Measure the proxy quality tier instead of assuming its speedup"
```

---

### Task 2: Rate keyed by quality

**Files:**
- Modify: `src/callsheet/farm_state.py`, `src/callsheet/forecast.py`
- Test: `tests/test_farm_state.py`, `tests/test_forecast.py`

**Interfaces:**
- Changes: `FarmState.mean_frame_ms` becomes `dict[tuple[str, str], float]` keyed `(shot_id, quality)`
- `forecast_all` looks up `(shot.id, shot.quality)`, falling back to `(shot.id, "final")`, then to `fallback_frame_ms`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_farm_state.py — replace the fixture and add
MEANS = json.dumps({"data": [
    {"metric": {"shot": "SH001", "quality": "final"}, "value": [0, "5146.94"]},
    {"metric": {"shot": "SH001", "quality": "proxy"}, "value": [0, "1930.11"]},
]})


def test_rate_is_keyed_by_shot_and_quality():
    state = parse_farm_state(MEANS)
    assert state.mean_frame_ms[("SH001", "final")] == pytest.approx(5146.94)
    assert state.mean_frame_ms[("SH001", "proxy")] == pytest.approx(1930.11)


def test_series_without_a_quality_label_is_ignored():
    """Pre-Phase-3 series carry no quality. They must not silently become 'final'."""
    legacy = json.dumps({"data": [{"metric": {"shot": "SH001"}, "value": [0, "5000"]}]})
    assert parse_farm_state(legacy).mean_frame_ms == {}
```

```python
# tests/test_forecast.py — add
def test_a_downgraded_shot_is_forecast_at_its_measured_proxy_rate():
    shots = [Shot("SH003", "c.blend", 256, [1, 2, 3], quality="proxy")]
    state = FarmState(mean_frame_ms={
        ("SH003", "final"): 26815.0,
        ("SH003", "proxy"): 7020.0,
    })
    review = Review("R", NOW + 3600, ["SH003"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.predicted_ms == pytest.approx(21060.0)
    assert forecast.estimate_source == "observed"


def test_a_quality_with_no_history_falls_back_to_final_and_says_so():
    shots = [Shot("SH003", "c.blend", 256, [1], quality="proxy")]
    state = FarmState(mean_frame_ms={("SH003", "final"): 26815.0})
    review = Review("R", NOW + 3600, ["SH003"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.predicted_ms == pytest.approx(26815.0)
    assert forecast.estimate_source == "fallback", (
        "using the final-quality rate for a proxy render is a guess, not a measurement"
    )
```

- [ ] **Step 2: Run to verify it fails**, then implement:

```python
# src/callsheet/farm_state.py
MEAN_QUERY = (
    'sum by (shot, quality) (rate(render_frame_duration_milliseconds_sum[1h]))'
    ' / sum by (shot, quality) (rate(render_frame_duration_milliseconds_count[1h]))'
)


def _by_shot_and_quality(raw: str) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], float] = {}
    for entry in _series(raw):
        metric = entry.get("metric", {})
        shot, quality = metric.get("shot"), metric.get("quality")
        if not shot or not quality:
            continue
        try:
            value = float(entry["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if math.isnan(value) or math.isinf(value):
            continue
        values[(shot, quality)] = value
    return values
```

```python
# src/callsheet/forecast.py — inside the loop, replacing the rate lookup
        observed = state.mean_frame_ms.get((shot.id, shot.quality))
        if observed is not None:
            per_frame, source = observed, "observed"
        elif (shot.id, "final") in state.mean_frame_ms:
            # Using the final rate for a proxy render is a guess, not a measurement.
            per_frame, source = state.mean_frame_ms[(shot.id, "final")], "fallback"
        else:
            per_frame, source = fallback_frame_ms, "fallback"
```

- [ ] **Step 3: Run the whole suite and fix every fixture the key change breaks**

Run: `python -m pytest -v`
`test_round.py` and `test_decide.py` both build `FarmState` with plain string
keys and will fail. Update them to tuples.

- [ ] **Step 4: Commit**

```bash
git add src/callsheet/farm_state.py src/callsheet/forecast.py tests/
git commit -m "Key observed render rate by quality tier, not shot alone"
```

---

### Task 3: The job queue

**Files:**
- Create: `src/callsheet/queue.py`
- Test: `tests/test_queue.py`

**Interfaces:**
- Produces: `Job(shot_id, frame, quality, state, position)`; `init_db(path)`, `enqueue_manifest(conn, shots)`, `snapshot(conn) -> list[Job]`, `mark_done(conn, shot_id, frame)`, `frames_done(conn) -> dict[str, int]`

States: `pending` | `rendering` | `done` | `preempted`. `position` is the render
order and is what makes queue-position reasoning enforceable.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queue.py
import sqlite3

from callsheet.domain import Shot
from callsheet.queue import enqueue_manifest, frames_done, init_db, mark_done, snapshot

SHOTS = [
    Shot("SH001", "a.blend", 16, [1, 2]),
    Shot("SH002", "b.blend", 64, [1]),
]


def _conn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def test_enqueue_creates_one_job_per_frame():
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    jobs = snapshot(conn)
    assert len(jobs) == 3
    assert all(job.state == "pending" for job in jobs)


def test_positions_preserve_manifest_order():
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    jobs = snapshot(conn)
    assert [job.position for job in jobs] == [0, 1, 2]
    assert [job.shot_id for job in jobs] == ["SH001", "SH001", "SH002"]


def test_frames_done_counts_only_completed_jobs():
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    assert frames_done(conn) == {}

    mark_done(conn, "SH001", 1)

    assert frames_done(conn) == {"SH001": 1}


def test_marking_done_twice_is_idempotent():
    """A retried worker must not inflate progress."""
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    mark_done(conn, "SH001", 1)
    mark_done(conn, "SH001", 1)
    assert frames_done(conn) == {"SH001": 1}


def test_enqueueing_twice_does_not_duplicate_jobs():
    conn = _conn()
    enqueue_manifest(conn, SHOTS)
    enqueue_manifest(conn, SHOTS)
    assert len(snapshot(conn)) == 3
```

- [ ] **Step 2: Run to verify it fails**, then implement:

```python
# src/callsheet/queue.py
"""The job queue. Source of truth for what work remains."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from callsheet.domain import Shot

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    shot_id  TEXT NOT NULL,
    frame    INTEGER NOT NULL,
    quality  TEXT NOT NULL DEFAULT 'final',
    state    TEXT NOT NULL DEFAULT 'pending',
    position INTEGER NOT NULL,
    PRIMARY KEY (shot_id, frame)
);
"""


@dataclass(frozen=True)
class Job:
    shot_id: str
    frame: int
    quality: str
    state: str
    position: int


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def enqueue_manifest(conn: sqlite3.Connection, shots: list[Shot]) -> None:
    """Idempotent: re-running against the same manifest changes nothing."""
    position = 0
    for shot in shots:
        for frame in shot.frames:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (shot_id, frame, quality, state, position)"
                " VALUES (?, ?, ?, 'pending', ?)",
                (shot.id, frame, shot.quality, position),
            )
            position += 1
    conn.commit()


def snapshot(conn: sqlite3.Connection) -> list[Job]:
    rows = conn.execute(
        "SELECT shot_id, frame, quality, state, position FROM jobs ORDER BY position"
    ).fetchall()
    return [Job(*row) for row in rows]


def mark_done(conn: sqlite3.Connection, shot_id: str, frame: int) -> None:
    conn.execute(
        "UPDATE jobs SET state = 'done' WHERE shot_id = ? AND frame = ?", (shot_id, frame)
    )
    conn.commit()


def frames_done(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT shot_id, COUNT(*) FROM jobs WHERE state = 'done' GROUP BY shot_id"
    ).fetchall()
    return {shot_id: count for shot_id, count in rows}
```

- [ ] **Step 3: Verify and commit**

```bash
git add src/callsheet/queue.py tests/test_queue.py
git commit -m "Add the SQLite job queue, source of truth for remaining work"
```

---

### Task 4: The structural guard

**Files:**
- Create: `src/callsheet/guard.py`
- Test: `tests/test_guard.py`

**Interfaces:**
- Produces: `rejected(actions, forecasts, at_risk_shot_id) -> list[tuple[Action, str]]`

Phase 2 taught the model about queue position through the prompt. A prompt is a
request, not a constraint — a model that ignores it produces an action the code
happily applies and annotates. This makes the invariant enforceable.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_guard.py
from callsheet.decide import Action
from callsheet.forecast import Forecast
from callsheet.guard import rejected

# Queue order: SH001 (index 0), SH002 (1), SH003 (2, at risk)
FORECASTS = [
    Forecast("SH001", 3, 15_000.0, 1_000_015, False, "observed"),
    Forecast("SH002", 3, 22_000.0, 1_000_037, False, "observed"),
    Forecast("SH003", 3, 80_000.0, 1_000_118, True, "observed"),
]


def test_preempting_a_shot_ahead_of_the_at_risk_shot_is_allowed():
    assert rejected([Action("SH002", "preempt", "cut")], FORECASTS, "SH003") == []


def test_preempting_a_shot_behind_the_at_risk_shot_is_rejected():
    """Freeing work that runs later recovers nothing for a shot that runs sooner."""
    result = rejected([Action("SH003", "preempt", "x")], FORECASTS, "SH001")
    assert len(result) == 1
    assert "behind" in result[0][1].lower()


def test_preempting_the_at_risk_shot_itself_is_rejected():
    result = rejected([Action("SH003", "preempt", "x")], FORECASTS, "SH003")
    assert len(result) == 1
    assert "itself" in result[0][1].lower()


def test_downgrading_the_at_risk_shot_is_always_allowed():
    """Downgrading the at-risk shot speeds up that very shot, wherever it sits."""
    assert rejected([Action("SH003", "downgrade", "x")], FORECASTS, "SH003") == []


def test_escalate_is_never_rejected():
    assert rejected([Action("SH003", "escalate", "x")], FORECASTS, "SH003") == []


def test_an_action_on_an_unknown_shot_is_rejected():
    result = rejected([Action("SH999", "preempt", "x")], FORECASTS, "SH003")
    assert len(result) == 1
    assert "unknown" in result[0][1].lower()
```

- [ ] **Step 2: Run to verify it fails**, then implement:

```python
# src/callsheet/guard.py
"""Rejects actions that cannot possibly help, before they are applied.

Phase 2 conveyed queue position to the model through the prompt. This makes it
a constraint rather than a request.
"""

from __future__ import annotations

from callsheet.decide import Action
from callsheet.forecast import Forecast


def rejected(
    actions: list[Action], forecasts: list[Forecast], at_risk_shot_id: str
) -> list[tuple[Action, str]]:
    order = {forecast.shot_id: index for index, forecast in enumerate(forecasts)}
    at_risk = order.get(at_risk_shot_id)

    problems: list[tuple[Action, str]] = []
    for action in actions:
        if action.action == "escalate":
            continue

        index = order.get(action.shot_id)
        if index is None:
            problems.append((action, f"unknown shot {action.shot_id}"))
            continue

        if action.action == "downgrade" and action.shot_id == at_risk_shot_id:
            continue

        if at_risk is None:
            continue

        if action.shot_id == at_risk_shot_id:
            problems.append((action, f"cannot {action.action} the at-risk shot itself"))
        elif index > at_risk:
            problems.append((
                action,
                f"{action.shot_id} is behind {at_risk_shot_id} in the queue, "
                "so changing it recovers no time",
            ))

    return problems
```

- [ ] **Step 3: Verify and commit**

```bash
git add src/callsheet/guard.py tests/test_guard.py
git commit -m "Reject actions that cannot recover time, before they are applied"
```

---

### Task 5: Apply actions and verify the residual

**Files:**
- Create: `src/callsheet/apply.py`, `src/callsheet/verify.py`
- Test: `tests/test_apply.py`, `tests/test_verify.py`

**Interfaces:**
- `apply_actions(shots, actions) -> list[Shot]` — pure; returns a new shot list with preempted shots removed and downgraded shots at `quality="proxy"`
- `Residual(shot_id, shortfall_s, closed)`; `verify(shots, actions, review, state, now_epoch_s) -> list[Residual]`

**This is the task the whole phase exists for.** `verify` re-runs `forecast_all`
over the post-action queue and reports what is *still* missing. It is pure
arithmetic and must never be handed to the model.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_apply.py
from callsheet.apply import apply_actions
from callsheet.decide import Action
from callsheet.domain import Shot

SHOTS = [
    Shot("SH001", "a.blend", 16, [1, 2, 3]),
    Shot("SH002", "b.blend", 64, [1, 2, 3]),
    Shot("SH003", "c.blend", 256, [1, 2, 3]),
]


def test_preempt_removes_the_shot_from_the_queue():
    result = apply_actions(SHOTS, [Action("SH002", "preempt", "cut")])
    assert [shot.id for shot in result] == ["SH001", "SH003"]


def test_downgrade_switches_the_shot_to_proxy_and_keeps_its_place():
    result = apply_actions(SHOTS, [Action("SH003", "downgrade", "x")])
    assert [shot.id for shot in result] == ["SH001", "SH002", "SH003"]
    assert result[2].quality == "proxy"


def test_escalate_changes_nothing():
    assert apply_actions(SHOTS, [Action("SH003", "escalate", "x")]) == SHOTS


def test_the_original_list_is_never_mutated():
    apply_actions(SHOTS, [Action("SH002", "preempt", "cut")])
    assert [shot.id for shot in SHOTS] == ["SH001", "SH002", "SH003"]
```

```python
# tests/test_verify.py
from callsheet.decide import Action
from callsheet.domain import FarmState, Review, Shot
from callsheet.verify import verify

NOW = 1_000_000
SHOTS = [
    Shot("SH001", "a.blend", 16, [1, 2, 3]),
    Shot("SH002", "b.blend", 64, [1, 2, 3]),
    Shot("SH003", "c.blend", 256, [1, 2, 3]),
]
STATE = FarmState(mean_frame_ms={
    ("SH001", "final"): 5147.0, ("SH001", "proxy"): 1930.0,
    ("SH002", "final"): 7341.0, ("SH002", "proxy"): 2750.0,
    ("SH003", "final"): 26815.0, ("SH003", "proxy"): 7020.0,
})


def test_an_insufficient_action_reports_the_residual_shortfall():
    """The Phase 2 case: preempting SH002 helps, but nowhere near enough."""
    review = Review("R", NOW + 30, ["SH003"])

    residuals = verify(SHOTS, [Action("SH002", "preempt", "cut")], review, STATE, NOW)

    sh003 = next(r for r in residuals if r.shot_id == "SH003")
    assert sh003.closed is False
    assert sh003.shortfall_s > 0


def test_a_sufficient_action_closes_the_gap():
    review = Review("R", NOW + 30, ["SH003"])

    residuals = verify(
        SHOTS,
        [Action("SH001", "preempt", "x"), Action("SH002", "preempt", "x"),
         Action("SH003", "downgrade", "x")],
        review, STATE, NOW,
    )

    sh003 = next(r for r in residuals if r.shot_id == "SH003")
    assert sh003.closed is True
    assert sh003.shortfall_s == 0


def test_no_actions_reports_the_original_shortfall():
    review = Review("R", NOW + 30, ["SH003"])
    residuals = verify(SHOTS, [], review, STATE, NOW)
    assert next(r for r in residuals if r.shot_id == "SH003").shortfall_s > 60


def test_only_required_shots_appear_in_the_residual():
    review = Review("R", NOW + 30, ["SH003"])
    residuals = verify(SHOTS, [], review, STATE, NOW)
    assert {r.shot_id for r in residuals} == {"SH003"}
```

- [ ] **Step 2: Run to verify they fail**, then implement:

```python
# src/callsheet/apply.py
"""Applies decision actions to a shot list. Pure: returns a new list."""

from __future__ import annotations

import dataclasses

from callsheet.decide import Action
from callsheet.domain import Shot


def apply_actions(shots: list[Shot], actions: list[Action]) -> list[Shot]:
    preempted = {a.shot_id for a in actions if a.action == "preempt"}
    downgraded = {a.shot_id for a in actions if a.action == "downgrade"}

    result = []
    for shot in shots:
        if shot.id in preempted:
            continue
        if shot.id in downgraded and shot.quality != "proxy":
            shot = dataclasses.replace(shot, quality="proxy")
        result.append(shot)
    return result
```

```python
# src/callsheet/verify.py
"""Did the decision actually work? Pure arithmetic, never the model's job."""

from __future__ import annotations

from dataclasses import dataclass

from callsheet.apply import apply_actions
from callsheet.decide import Action
from callsheet.domain import FarmState, Review, Shot
from callsheet.forecast import forecast_all


@dataclass(frozen=True)
class Residual:
    shot_id: str
    shortfall_s: int
    closed: bool


def verify(
    shots: list[Shot],
    actions: list[Action],
    review: Review,
    state: FarmState,
    now_epoch_s: int,
) -> list[Residual]:
    """Re-forecast the queue as it would be after the actions are applied."""
    after = apply_actions(shots, actions)
    forecasts = forecast_all(after, review, state, now_epoch_s)
    by_id = {forecast.shot_id: forecast for forecast in forecasts}

    residuals = []
    for shot_id in review.required_shots:
        forecast = by_id.get(shot_id)
        if forecast is None:
            # A required shot was preempted — the worst possible outcome.
            residuals.append(Residual(shot_id, shortfall_s=-1, closed=False))
            continue
        shortfall = max(0, forecast.finishes_at_epoch_s - review.deadline_epoch_s)
        residuals.append(Residual(shot_id, shortfall, closed=shortfall == 0))

    return residuals
```

- [ ] **Step 3: Verify and commit**

```bash
git add src/callsheet/apply.py src/callsheet/verify.py tests/test_apply.py tests/test_verify.py
git commit -m "Verify whether a decision actually closes the deadline gap"
```

---

### Task 6: Wire verification into the round

**Files:**
- Modify: `src/callsheet/round.py`, `src/callsheet/annotate.py`
- Test: `tests/test_round.py`, `tests/test_annotate.py`

**Interfaces:**
- `RoundResult` gains `residuals: list[Residual]` and `guard_rejections: list[tuple[Action, str]]`
- The annotation text states whether the gap was closed

- [ ] **Step 1: Write the failing test**

```python
# tests/test_round.py — add
@pytest.mark.asyncio
async def test_an_insufficient_decision_is_reported_as_such_not_as_success():
    """The system must never claim to have fixed something it has not."""
    state = FarmState(mean_frame_ms={("SH001", "final"): 60_000.0})
    review = Review("R", NOW + 10, ["SH001"])
    decision = Decision("done", [Action("SH001", "downgrade", "x")])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", return_value=decision), \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    assert result.residuals
    assert result.residuals[0].closed is False
    assert result.residuals[0].shortfall_s > 0


@pytest.mark.asyncio
async def test_guard_rejected_actions_are_recorded_and_not_applied():
    state = FarmState(mean_frame_ms={("SH001", "final"): 60_000.0})
    review = Review("R", NOW + 10, ["SH001"])
    decision = Decision("bad", [Action("SH001", "preempt", "sacrificing the required shot")])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", return_value=decision), \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    assert result.guard_rejections, "preempting the at-risk shot must be rejected"
```

```python
# tests/test_annotate.py — add
def test_annotation_states_when_the_gap_was_not_closed():
    from callsheet.verify import Residual

    payload = build_annotation(DECISION, now_epoch_s=1_000_000,
                               residuals=[Residual("SH003", 66, False)])
    assert "66" in payload["text"]
    assert "not closed" in payload["text"].lower() or "still" in payload["text"].lower()
```

- [ ] **Step 2: Implement**

**`RoundResult` gains two fields, and they need `default_factory`.** A bare
`residuals: list[Residual] = []` is a `ValueError` at class-definition time —
dataclasses reject mutable defaults. They must also follow the already-defaulted
`degraded_reason`.

```python
# src/callsheet/round.py
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoundResult:
    forecasts: list[Forecast]
    decision: Decision | None
    annotation_written: bool
    degraded_reason: str | None = None
    residuals: list[Residual] = field(default_factory=list)
    guard_rejections: list[tuple[Action, str]] = field(default_factory=list)
```

**All four `return` statements must be updated, not just the first.** Phase 2's
`run_round` returns in four places: healthy farm, model failure, annotation
failure, and success. The success return is the one the new tests read, so
missing it makes `result.residuals` empty on the exact path that matters.

Phase 2's version also discards the miss list — `if not misses(forecasts):` never
binds the result, so `missing` below would be a `NameError`. Bind it:

```python
    missing = misses(forecasts)
    if not missing:
        return RoundResult(forecasts, None, False)

    try:
        decision = decide(config, shots, review, forecasts)
    except Exception as error:      # noqa: BLE001
        return RoundResult(forecasts, None, False, degraded_reason=str(error))

    # Anchor the guard on the EARLIEST missing required shot. `misses` returns
    # queue order, so this is deliberately conservative: an action that would
    # rescue a later missing shot is rejected as "behind". A policy choice,
    # written down rather than arrived at by accident.
    at_risk = missing[0].shot_id

    guard_rejections = rejected(decision.actions, forecasts, at_risk)
    blocked = {id(action) for action, _ in guard_rejections}
    allowed = [action for action in decision.actions if id(action) not in blocked]
    residuals = verify(shots, allowed, review, state, now_epoch_s)

    try:
        await write_annotation(config, decision, now_epoch_s,
                               residuals=residuals, applied=allowed,
                               rejections=guard_rejections)
    except Exception as error:      # noqa: BLE001
        return RoundResult(forecasts, decision, False, f"annotation failed: {error}",
                           residuals, guard_rejections)

    return RoundResult(forecasts, decision, True, None, residuals, guard_rejections)
```

Identity rather than equality when filtering: `Action` is a frozen dataclass, so
two distinct actions with identical fields compare equal, and `action not in
{...}` would drop a legitimate action that merely matched a rejected one.

**Both** `build_annotation` and `write_annotation` take the new arguments. Giving
them only to `build_annotation` would leave a green unit test sitting over
production wiring that never runs — the worst possible outcome in the one phase
about not claiming success falsely.

The annotation renders **`applied`, not `decision.actions`**. A guard-rejected
action that still appears in Grafana is the same dishonesty as an unreported
residual, one level down.

```python
# src/callsheet/annotate.py
from callsheet.verify import Residual


def build_annotation(
    decision: Decision,
    now_epoch_s: int,
    residuals: list[Residual] | None = None,
    applied: list[Action] | None = None,
    rejections: list[tuple[Action, str]] | None = None,
) -> dict:
    """Render the decision as Grafana sees it.

    `applied` defaults to every action in the decision, but when the guard has
    blocked some, only the surviving ones are reported as taken.
    """
    actions = decision.actions if applied is None else applied
    detail = "; ".join(
        f"{action.action} {action.shot_id} ({action.reason})" for action in actions
    )

    text = f"CALLSHEET: {decision.summary}"
    if detail:
        text += f" — {detail}"

    for action, why in rejections or []:
        text += f" — REJECTED {action.action} {action.shot_id}: {why}"

    unclosed = [residual for residual in (residuals or []) if not residual.closed]
    if unclosed:
        shortfalls = ", ".join(
            f"{residual.shot_id} still short by {residual.shortfall_s}s" for residual in unclosed
        )
        text += f" — GAP NOT CLOSED: {shortfalls}"

    return {
        "text": text,
        "time": now_epoch_s * 1000,
        "tags": ["callsheet", "scheduling-decision"],
    }


async def write_annotation(
    config: Config,
    decision: Decision,
    now_epoch_s: int,
    residuals: list[Residual] | None = None,
    applied: list[Action] | None = None,
    rejections: list[tuple[Action, str]] | None = None,
) -> str:
    return await call_tool(
        config,
        "create_annotation",
        build_annotation(decision, now_epoch_s, residuals, applied, rejections),
    )
```

Add a test that the *production* path carries the gap, not just the builder:

```python
# tests/test_annotate.py
@pytest.mark.asyncio
async def test_write_annotation_passes_the_residual_through_to_the_payload():
    """Guards against the builder knowing about gaps while the writer does not."""
    from unittest.mock import AsyncMock, patch

    from callsheet.verify import Residual

    with patch("callsheet.annotate.call_tool", AsyncMock(return_value="ok")) as call:
        await write_annotation(CONFIG, DECISION, 1_000_000, residuals=[Residual("SH003", 66, False)])

    assert "66" in call.call_args[0][2]["text"]


def test_a_rejected_action_is_not_reported_as_taken():
    payload = build_annotation(
        DECISION, 1_000_000,
        applied=[],
        rejections=[(DECISION.actions[0], "behind the at-risk shot")],
    )
    assert "REJECTED" in payload["text"]
    assert "preempt SH002 (already cut)" not in payload["text"]
```

`CONFIG` in that module is the same keyword-constructed `Config` used in
`tests/test_round.py`.

- [ ] **Step 3: Run the whole suite, verify, commit**

```bash
git add src/callsheet/round.py src/callsheet/annotate.py tests/
git commit -m "Report whether the decision closed the gap, in the round and the annotation"
```

---

### Task 7: Honest demo

**Files:**
- Modify: `scripts/demo_round.py`, `README.md`

- [ ] **Step 1: Print the before/after and the residual**

The demo must show three things in sequence: the forecast, the decision, and
**whether the decision worked**. Rejected actions are printed too — a guard that
fires silently teaches a viewer nothing.

```python
    print(f"\nCALL SHEET: {result.decision.summary}")
    for action in result.decision.actions:
        print(f"  {action.action.upper():10} {action.shot_id} — {action.reason}")

    for action, why in result.guard_rejections:
        print(f"  REJECTED   {action.shot_id} — {why}")

    print("\nAfter applying the plan:")
    for residual in result.residuals:
        if residual.closed:
            print(f"  CLOSED  {residual.shot_id} makes the review")
        else:
            print(f"  STILL SHORT  {residual.shot_id} by {residual.shortfall_s}s")
```

**The closing line must change too.** Phase 2 ends with
`PASS: observe -> forecast -> decide -> annotate completed end to end`, which
reads as "it worked" and would re-commit the exact sin this phase fixes — in
front of a judge. Replace it:

```python
    closed = all(residual.closed for residual in result.residuals)
    if closed:
        print("\nPASS: the plan closes the deadline gap.")
    else:
        short = ", ".join(
            f"{r.shot_id} by {r.shortfall_s}s" for r in result.residuals if not r.closed
        )
        print(f"\nPASS: loop completed and reported honestly — gap NOT closed ({short}).")
    return 0
```

Both are exit 0. The system's job here is to tell the truth about the outcome,
not to guarantee a good one.

- [ ] **Step 2: Run it and record the output**

Run: `python scripts/demo_round.py`

**Exit 0 no longer requires the gap to be closed** — it requires the system to
report the truth about it. An unclosed residual that is correctly reported is a
pass; an unclosed residual reported as success is the bug this phase fixes.

- [ ] **Step 3: Update the README, commit**

The README gains a **How it works** section stating the loop in four steps
(observe → forecast → decide → verify), and one sentence that the system reports
an unclosed gap rather than claiming success. Keep the existing setup and run
instructions; add `python scripts/demo_round.py` alongside the spike command.

```bash
git add scripts/demo_round.py README.md
git commit -m "Show whether the plan actually closes the deadline gap"
```

---

## What Phase 3 deliberately does not build

No shot board — that is Plan 4, and it is where the Design criterion is won. No
ablation, no deploy, no logs pipeline.

**The queue is built but not yet driving the workers.** Task 3 creates it and
`frames_done` reads from it, but `run_manifest` still renders the manifest
linearly rather than claiming jobs. Wiring the workers to the queue belongs with
the board, since the board renders queue state and the two want the same schema
settled at once.

## Definition of done for Phase 3

- [ ] `python -m pytest` green with no credentials
- [ ] `python -m pytest -m integration` green with credentials
- [ ] The measured proxy speedup is recorded in the design doc, with its number
- [ ] `python scripts/demo_round.py` prints a residual, closed or not
- [ ] An insufficient decision is provably reported as insufficient
- [ ] Phase 3 findings appended to the design doc
