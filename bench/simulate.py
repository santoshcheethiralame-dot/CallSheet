"""The farm, as arithmetic: one serial queue advancing a millisecond clock.

The same loop runs all three arms. A policy is only ever consulted through
`replan`, so the arms differ in what they decide and in nothing else — no arm
gets a different simulator, a different clock, or a different cost table.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from callsheet.domain import FarmState, Shot

from bench.workload import CostTable, Night

ROUND_MS = 30_000.0
"""§16: the board re-plans every 30 seconds. The CALLSHEET arm re-plans on the
same cadence, so the harness is not quietly giving the agent a faster loop than
the product has."""


@dataclass
class Progress:
    """What the scheduler is allowed to know, mid-night.

    Deliberately the same two things the real round gets: `frames_done`, which
    §16 says comes from the queue, and observed per-frame means, which §13 says
    come from telemetry. Nothing here can see the cost table — a policy that
    could would be an oracle, and every number it produced would be worthless.
    """

    frames_done: dict[str, int] = field(default_factory=dict)
    observed_sum: dict[tuple[str, str], float] = field(default_factory=dict)
    observed_n: dict[tuple[str, str], int] = field(default_factory=dict)
    clock_ms: float = 0.0

    def farm_state(self, prior: dict[tuple[str, str], float]) -> FarmState:
        """Telemetry as the forecaster will see it.

        `prior` is the previous nights' measured mean for each (shot, tier) —
        real history, and systematically wrong for tonight by this run's
        multiplier. Frames rendered tonight override it as they land, which is
        exactly the drift the product lives with. It is not a peek at the answer:
        the prior is the same for every run, and tonight's frames are only known
        after they have already been paid for.
        """
        means = dict(prior)
        for key, total in self.observed_sum.items():
            means[key] = total / self.observed_n[key]
        return FarmState(mean_frame_ms=means, frames_done=dict(self.frames_done))


@dataclass(frozen=True)
class RunResult:
    """One night under one policy."""

    required_delivered: int
    required_total: int
    deadline_misses: int
    cut_node_s: float
    """Node-seconds burned on shots the director had already cut."""

    total_node_s: float
    unrequired_node_s: float
    """Node-seconds on shots this review did not require — cut or otherwise."""

    required_delivered_at_proxy: int
    """Required shots delivered, but only because they were downgraded. The
    price of the win, and the reason this column is in the table."""

    unrequired_delivered: int
    """Shots this review did not require, but which got finished anyway.

    The metric that can go *against* CALLSHEET, and the reason it is here.
    Preempting a shot does not merely stop wasting capacity on it: the shot is
    gone, and tomorrow's review may want it. A scheduler that clears the queue
    to save tonight has borrowed from a night this harness does not simulate.
    """

    escalations: int
    preempted: int


def run_night(night: Night, costs: CostTable, order: list[Shot],
              replan=None, round_ms: float = ROUND_MS) -> RunResult:
    """Render `order` serially until the deadline, re-planning if asked to.

    A frame dispatched before the deadline runs to completion and is charged in
    full; the farm does not know a frame is about to be late and killing a job
    mid-frame throws the work away. So the clock can end past the deadline, and
    a shot whose last frame lands after it has still missed.
    """
    queue = list(order)
    done: dict[str, int] = defaultdict(int)
    spent_ms: dict[str, float] = defaultdict(float)
    finished_at_ms: dict[str, float] = {}
    qualities_used: dict[str, set[str]] = defaultdict(set)
    frames_of = {shot.id: len(shot.frames) for shot in night.shots}

    progress = Progress()
    escalations = 0
    next_replan_ms = 0.0

    while progress.clock_ms < night.deadline_ms:
        if replan is not None and progress.clock_ms >= next_replan_ms:
            queue, plan_escalations = replan(queue, progress)
            escalations += plan_escalations
            next_replan_ms = progress.clock_ms + round_ms

        pending = [shot for shot in queue if done[shot.id] < frames_of[shot.id]]
        if not pending:
            break

        shot = pending[0]
        frame = shot.frames[done[shot.id]]
        quality = shot.quality
        duration = costs.cost(shot.id, frame, quality)

        progress.clock_ms += duration
        spent_ms[shot.id] += duration
        done[shot.id] += 1
        qualities_used[shot.id].add(quality)

        key = (shot.id, quality)
        progress.observed_sum[key] = progress.observed_sum.get(key, 0.0) + duration
        progress.observed_n[key] = progress.observed_n.get(key, 0) + 1
        progress.frames_done[shot.id] = done[shot.id]

        if done[shot.id] == frames_of[shot.id]:
            finished_at_ms[shot.id] = progress.clock_ms

    by_id = {shot.id: shot for shot in night.shots}
    required = night.review.required_shots

    delivered = [
        shot_id for shot_id in required
        if finished_at_ms.get(shot_id, float("inf")) <= night.deadline_ms
    ]
    at_proxy = [
        shot_id for shot_id in delivered if "proxy" in qualities_used[shot_id]
    ]

    return RunResult(
        required_delivered=len(delivered),
        required_total=len(required),
        deadline_misses=len(required) - len(delivered),
        cut_node_s=sum(ms for sid, ms in spent_ms.items() if by_id[sid].is_cut) / 1000,
        total_node_s=sum(spent_ms.values()) / 1000,
        unrequired_node_s=sum(
            ms for sid, ms in spent_ms.items() if sid not in required
        ) / 1000,
        required_delivered_at_proxy=len(at_proxy),
        unrequired_delivered=sum(
            1 for shot in night.shots
            if shot.id not in required
            and finished_at_ms.get(shot.id, float("inf")) <= night.deadline_ms
        ),
        escalations=escalations,
        preempted=len(night.shots) - len(queue),
    )
