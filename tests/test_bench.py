"""The ablation's own tests. The policies are pure, so they are testable.

A benchmark is a claim, and an untested benchmark is an unsupported one. These
pin four things: the arms are pure and reproducible, every arm sees the same
sampled costs, the sampler matches the variance the design doc measured, and —
the one that matters most for honesty — the workload where CALLSHEET does *not*
win still does not win.
"""

from __future__ import annotations

import ast
import statistics
from pathlib import Path

from callsheet.domain import FarmState, Review, Shot

from bench import policies, run, simulate, workload
from bench.policies import ARMS, callsheet, fifo, plan, priority_only, run_arm
from bench.workload import MEASURED_MS, Night, generate_night, sample_costs

BENCH_DIR = Path(__file__).resolve().parent.parent / "bench"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def make_shot(shot_id: str, frames: int = 1, priority: int = 50,
              is_cut: bool = False, quality: str = "final") -> Shot:
    return Shot(id=shot_id, scene=f"scenes/{shot_id}.blend", samples=64,
                frames=list(range(1, frames + 1)), priority=priority,
                quality=quality, is_cut=is_cut)


def make_night(shots: list[Shot], required: list[str], deadline_s: int,
               classes: dict[str, str] | None = None) -> Night:
    return Night(
        shots=shots,
        review=Review("Test review", deadline_s, required),
        shot_class=classes or {shot.id: "medium" for shot in shots},
        deadline_ms=deadline_s * 1000.0,
    )


def flat_state(shots: list[Shot], final_ms: float, proxy_ms: float,
               frames_done: dict[str, int] | None = None) -> FarmState:
    return FarmState(
        mean_frame_ms={
            key: value
            for shot in shots
            for key, value in (((shot.id, "final"), final_ms),
                               ((shot.id, "proxy"), proxy_ms))
        },
        frames_done=frames_done or {},
    )


# --------------------------------------------------------------------------
# reproducibility — the property the whole result rests on
# --------------------------------------------------------------------------

def test_the_same_seed_draws_the_same_costs():
    night = generate_night(1)
    assert sample_costs(night, 7).frame_ms == sample_costs(night, 7).frame_ms


def test_a_different_seed_draws_different_costs():
    night = generate_night(1)
    assert sample_costs(night, 7).frame_ms != sample_costs(night, 8).frame_ms


def test_the_same_seed_generates_the_same_night():
    first, second = generate_night(3), generate_night(3)
    assert first.shots == second.shots
    assert first.review == second.review
    assert first.deadline_ms == second.deadline_ms


def test_the_whole_table_reproduces_from_a_seed():
    """The headline claim: run it twice, get the same numbers."""
    first = run.collect(2, 3, 99, 12, 1.25, 0.4)
    second = run.collect(2, 3, 99, 12, 1.25, 0.4)
    assert first == second


def test_a_different_seed_moves_the_table():
    first, *_ = run.collect(2, 3, 99, 12, 1.25, 0.4)
    second, *_ = run.collect(2, 3, 100, 12, 1.25, 0.4)
    assert first != second


# --------------------------------------------------------------------------
# fairness — every arm must face the same luck
# --------------------------------------------------------------------------

def test_every_arm_is_pure():
    """Running an arm twice on the same inputs gives the same answer.

    If an arm mutated the night or the cost table, the second call would differ
    and every later arm in `collect` would be running against a changed world.
    """
    night = generate_night(5)
    costs = sample_costs(night, 11)
    for arm in ARMS:
        assert run_arm(arm, night, costs) == run_arm(arm, night, costs)


def test_an_arm_does_not_disturb_the_arms_after_it():
    """Order of evaluation must not change any arm's result."""
    night = generate_night(5)
    costs = sample_costs(night, 11)
    forwards = [run_arm(arm, night, costs) for arm in ARMS]
    backwards = [run_arm(arm, night, costs) for arm in reversed(ARMS)]
    assert forwards == list(reversed(backwards))


def test_an_arm_cannot_change_the_shots_it_was_given():
    night = generate_night(5)
    before = list(night.shots)
    callsheet(night, sample_costs(night, 11))
    assert night.shots == before


def test_the_cost_table_covers_both_tiers_of_every_frame():
    """The pre-sampling that makes a downgrade cost the same in any arm."""
    night = generate_night(5)
    costs = sample_costs(night, 11)
    expected = {
        (shot.id, frame, quality)
        for shot in night.shots for frame in shot.frames
        for quality in ("final", "proxy")
    }
    assert set(costs.frame_ms) == expected


# --------------------------------------------------------------------------
# the cost model matches what the design doc measured
# --------------------------------------------------------------------------

def test_frame_costs_centre_on_the_measured_means():
    night = generate_night(2, n_shots=40)
    draws: dict[tuple[str, str], list[float]] = {}
    for seed in range(60):
        costs = sample_costs(night, seed)
        for (shot_id, _frame, quality), value in costs.frame_ms.items():
            key = (night.shot_class[shot_id], quality)
            draws.setdefault(key, []).append(value)

    for (klass, quality), values in draws.items():
        measured = MEASURED_MS[klass][quality]
        assert abs(statistics.fmean(values) - measured) / measured < 0.02


def test_the_across_run_swing_never_exceeds_the_measured_one_and_a_half():
    """§12: 1.5x between runs on identical work. Not 3x, and not 1.05x."""
    night = generate_night(2, n_shots=30)
    per_seed = [
        statistics.fmean([
            value for (shot_id, _f, quality), value in sample_costs(night, seed).frame_ms.items()
            if night.shot_class[shot_id] == "heavy" and quality == "final"
        ])
        for seed in range(200)
    ]
    swing = max(per_seed) / min(per_seed)
    assert 1.0 < swing <= workload.ACROSS_RUN_SWING * (1 + workload.WITHIN_RUN_SPREAD) ** 2


def test_within_run_frames_of_one_shot_stay_inside_the_measured_spread():
    """§15: three consecutive frames landed within 3.5% of each other."""
    night = generate_night(2, n_shots=20)
    costs = sample_costs(night, 4)
    for shot in night.shots:
        values = [costs.cost(shot.id, frame, "final") for frame in shot.frames]
        assert max(values) / min(values) <= (1 + workload.WITHIN_RUN_SPREAD) / (
            1 - workload.WITHIN_RUN_SPREAD
        )


# --------------------------------------------------------------------------
# FIFO and priority-only react to nothing. That is their whole definition.
# --------------------------------------------------------------------------

def test_fifo_renders_the_manifest_in_order_and_drops_nothing():
    shots = [make_shot("A", 2), make_shot("B", 2), make_shot("C", 2)]
    night = make_night(shots, required=["C"], deadline_s=10_000)
    costs = sample_costs(night, 1)
    result = fifo(night, costs)
    assert result.deadline_misses == 0
    assert result.unrequired_delivered == 2
    assert result.required_delivered_at_proxy == 0


def test_priority_only_orders_by_priority_and_keeps_manifest_order_on_ties():
    shots = [make_shot("A", priority=10), make_shot("B", priority=90),
             make_shot("C", priority=90)]
    night = make_night(shots, required=["A"], deadline_s=1)
    # Deadline of 1s: only the first shot in render order can possibly land.
    costs = sample_costs(night, 1)
    result = priority_only(night, costs)
    # B outranks A, and C ties B but sits later in the manifest, so B renders
    # first and required shot A misses. Priority-only cannot notice.
    assert result.deadline_misses == 1


def test_neither_static_arm_ever_downgrades_or_escalates():
    night = generate_night(6)
    costs = sample_costs(night, 2)
    for arm in (fifo, priority_only):
        result = arm(night, costs)
        assert result.required_delivered_at_proxy == 0
        assert result.escalations == 0
        assert result.preempted == 0


# --------------------------------------------------------------------------
# the CALLSHEET planner
# --------------------------------------------------------------------------

def test_a_healthy_farm_produces_no_actions():
    """The same property the product depends on to stay inside the free tier."""
    shots = [make_shot("A"), make_shot("B")]
    night = make_night(shots, required=["B"], deadline_s=1000)
    assert plan(shots, night, 0, flat_state(shots, 10_000, 5_000)) == []


def test_it_preempts_a_non_required_shot_ahead_of_the_at_risk_shot():
    shots = [make_shot("A"), make_shot("B"), make_shot("C")]
    night = make_night(shots, required=["C"], deadline_s=15)
    actions = plan(shots, night, 0, flat_state(shots, 10_000, 5_000))
    assert [(a.shot_id, a.action) for a in actions] == [("A", "preempt"), ("B", "preempt")]


def test_it_never_preempts_a_shot_the_review_requires():
    night = generate_night(8)
    costs = sample_costs(night, 3)
    result = callsheet(night, costs)
    # A preempted required shot is the one outcome `verify` calls unforgivable.
    # Delivered + missed must still account for every required shot.
    assert result.required_delivered + result.deadline_misses == result.required_total


def test_it_gives_up_a_cut_shot_before_an_uncut_one():
    """§14's ordering: work the director already threw away is free."""
    shots = [make_shot("A", priority=10), make_shot("B", priority=95, is_cut=True),
             make_shot("C")]
    night = make_night(shots, required=["C"], deadline_s=25)
    actions = plan(shots, night, 0, flat_state(shots, 10_000, 5_000))
    assert actions[0].shot_id == "B"


def test_it_never_preempts_a_shot_that_has_nothing_left_to_render():
    """A finished shot returns no time; sacrificing it costs a shot for free."""
    shots = [make_shot("A"), make_shot("B"), make_shot("C")]
    night = make_night(shots, required=["C"], deadline_s=15)
    state = flat_state(shots, 10_000, 5_000, frames_done={"A": 1})
    actions = plan(shots, night, 0, state)
    assert "A" not in {action.shot_id for action in actions}


def test_it_downgrades_the_at_risk_shot_when_nothing_ahead_can_be_given_up():
    shots = [make_shot("C")]
    night = make_night(shots, required=["C"], deadline_s=15)
    actions = plan(shots, night, 0, flat_state(shots, 30_000, 10_000))
    assert [(a.shot_id, a.action) for a in actions] == [("C", "downgrade")]


def test_it_does_not_downgrade_a_shot_the_downgrade_cannot_save():
    """§14 measured a 1.08x proxy speedup on a cheap shot. A downgrade that
    still misses the review is a quality loss bought with nothing."""
    shots = [make_shot("C")]
    night = make_night(shots, required=["C"], deadline_s=15)
    # Proxy barely helps: 30s -> 29s, still eleven seconds late.
    actions = plan(shots, night, 0, flat_state(shots, 30_000, 29_000))
    assert [(a.shot_id, a.action) for a in actions] == [("C", "escalate")]


def test_it_escalates_rather_than_reporting_a_plan_that_does_not_work():
    shots = [make_shot("A"), make_shot("C", frames=5)]
    night = make_night(shots, required=["C"], deadline_s=5)
    actions = plan(shots, night, 0, flat_state(shots, 10_000, 9_500))
    assert actions[-1].action == "escalate"


def test_the_guard_runs_in_front_of_whatever_proposed_the_actions():
    """`plan` returns only what `callsheet.guard` let through.

    Handed a proposal to sacrifice a shot sitting *behind* the at-risk one —
    the exact inert move §13 caught the model making — nothing survives.
    """
    shots = [make_shot("C"), make_shot("Z")]
    night = make_night(shots, required=["C"], deadline_s=15)
    state = flat_state(shots, 30_000, 30_000)

    original = policies.choose_sacrifices
    try:
        policies.choose_sacrifices = lambda *_args, **_kwargs: [
            policies.Action("Z", "preempt", "behind the at-risk shot")
        ]
        assert plan(shots, night, 0, state) == []
    finally:
        policies.choose_sacrifices = original


# --------------------------------------------------------------------------
# the simulator
# --------------------------------------------------------------------------

def test_the_scheduler_is_never_shown_a_frame_that_has_not_rendered():
    """`Progress` is the only channel into a policy, and it carries history."""
    progress = simulate.Progress(frames_done={"A": 2},
                                 observed_sum={("A", "final"): 20_000.0},
                                 observed_n={("A", "final"): 2})
    state = progress.farm_state({("A", "final"): 99_000.0, ("B", "final"): 99_000.0})
    assert state.mean_frame_ms[("A", "final")] == 10_000.0   # tonight's evidence
    assert state.mean_frame_ms[("B", "final")] == 99_000.0   # prior nights only
    assert state.frames_done == {"A": 2}


def test_a_frame_dispatched_before_the_deadline_is_charged_in_full():
    """The farm cannot know a frame will be late, and killing it wastes the work."""
    shots = [make_shot("A")]
    night = make_night(shots, required=[], deadline_s=1)
    costs = workload.CostTable({("A", 1, "final"): 30_000.0, ("A", 1, "proxy"): 1.0})
    result = simulate.run_night(night, costs, shots)
    assert result.total_node_s == 30.0


def test_a_shot_finishing_after_the_deadline_has_missed_it():
    shots = [make_shot("A")]
    night = make_night(shots, required=["A"], deadline_s=1)
    costs = workload.CostTable({("A", 1, "final"): 30_000.0, ("A", 1, "proxy"): 1.0})
    result = simulate.run_night(night, costs, shots)
    assert result.deadline_misses == 1


def test_cut_node_seconds_count_only_shots_the_director_cut():
    shots = [make_shot("A", is_cut=True), make_shot("B")]
    night = make_night(shots, required=[], deadline_s=1000)
    costs = workload.CostTable({
        ("A", 1, "final"): 4_000.0, ("A", 1, "proxy"): 1.0,
        ("B", 1, "final"): 6_000.0, ("B", 1, "proxy"): 1.0,
    })
    result = simulate.run_night(night, costs, shots)
    assert result.cut_node_s == 4.0
    assert result.total_node_s == 10.0


# --------------------------------------------------------------------------
# the claim, and its boundary
# --------------------------------------------------------------------------

def test_callsheet_delivers_more_required_shots_on_the_headline_workload():
    table, _runs, _required = run.collect(3, 8, run.DEFAULT_SEED, 16, 1.25, 0.4)
    delivered = {arm: table[arm]["required_delivered"].mean for arm in ARMS}
    assert delivered["CALLSHEET"] > delivered["Priority-only"] > delivered["FIFO"]


def test_the_advantage_disappears_when_there_is_nothing_to_sacrifice():
    """The honest boundary of the claim, pinned so it cannot quietly rot.

    Every shot required, deadline unreachable: preemption is unavailable and no
    downgrade closes the gap, so CALLSHEET correctly takes no action at all and
    lands exactly on FIFO. Priority-only beats both, because reordering the
    queue is a lever CALLSHEET's repertoire does not contain.
    """
    table, _runs, _required = run.collect(3, 8, run.DEFAULT_SEED, 16, 0.85, 1.0)
    delivered = {arm: table[arm]["required_delivered"].mean for arm in ARMS}
    assert delivered["CALLSHEET"] == delivered["FIFO"]
    assert delivered["Priority-only"] > delivered["CALLSHEET"]
    assert table["CALLSHEET"]["escalations"].mean > 0


# --------------------------------------------------------------------------
# the honesty claim is structural, not just prose
# --------------------------------------------------------------------------

def test_the_harness_never_calls_the_language_model():
    """The disclaimer has to be enforceable, or it is decoration.

    Parsed rather than grepped, so a docstring may say the word `decide()` — as
    `bench/__init__.py` does, explaining why it is absent — while a call to it
    still fails the test. `Action` is a dataclass and importing it costs
    nothing; `decide` is the function that spends Gemini quota, and no arm may
    reach it. A benchmark that quietly called the model would be claiming a
    result about the model.
    """
    for path in sorted(BENCH_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "callsheet.decide":
                assert [alias.name for alias in node.names] == ["Action"], (
                    f"{path.name} imports more than Action from callsheet.decide"
                )
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                module = getattr(node, "module", None) or ""
                assert "genai" not in f"{module} {' '.join(names)}", (
                    f"{path.name} reaches for the model client"
                )
            if isinstance(node, ast.Call):
                called = node.func
                name = getattr(called, "attr", None) or getattr(called, "id", None)
                assert name != "decide", f"{path.name} calls decide()"


def test_the_callsheet_arm_uses_the_real_modules_rather_than_its_own():
    """If the arm reimplemented the forecaster it would measure the benchmark."""
    text = (BENCH_DIR / "policies.py").read_text(encoding="utf-8")
    for module in ("callsheet.forecast", "callsheet.guard", "callsheet.verify",
                   "callsheet.apply"):
        assert f"from {module} import" in text


def test_the_report_states_what_it_does_not_measure():
    assert "DOES NOT MEASURE" in run.CAVEAT
    assert "20 requests per day" in run.CAVEAT
