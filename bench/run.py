"""`python -m bench.run` — the §6 table, filled in.

Every printed line is ASCII (§14: a console on a legacy codepage must not be
able to kill the run mid-sentence).
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass

from bench.policies import ARMS, run_arm
from bench.workload import (
    DEFAULT_REQUIRED_FRACTION,
    DEFAULT_SHOTS,
    DEFAULT_SLACK,
    generate_night,
    sample_costs,
)

DEFAULT_NIGHTS = 5
DEFAULT_REPEATS = 40
DEFAULT_SEED = 20260807

CAVEAT = """\
WHAT THIS DOES NOT MEASURE
  Not the model. Gemini's free tier is 20 requests per day, so a few hundred
  runs cannot call it and this harness does not pretend to. The CALLSHEET arm
  runs the real forecaster, the real guard and the real verifier, but the
  choice of which shot to sacrifice is made by a deterministic rule standing in
  for Gemini. The model's actual job - picking among sacrifices that all close
  the gap but cost the production different things - is not evaluated here.
  This is evidence about the scheduling policy the agent operates within. It is
  not evidence about the agent's judgement.

  Not Blender either. Frame costs are sampled from the means measured in
  section 14 of the design doc, with the run-to-run variance recorded in
  sections 12, 14 and 15.
"""


@dataclass(frozen=True)
class Column:
    mean: float
    low: float
    high: float
    stdev: float

    @classmethod
    def of(cls, values: list[float]) -> "Column":
        return cls(
            mean=statistics.fmean(values),
            low=min(values),
            high=max(values),
            stdev=statistics.stdev(values) if len(values) > 1 else 0.0,
        )

    def cell(self, places: int = 2) -> str:
        return (f"{self.mean:.{places}f} +/- {self.stdev:.{places}f} "
                f"({self.low:.{places}f}-{self.high:.{places}f})")


def collect(nights: int, repeats: int, seed: int, n_shots: int, slack: float,
            required_fraction: float) -> tuple[dict[str, dict[str, Column]], int, int]:
    """Every arm, every run, on identical sampled costs.

    The cost seed depends only on (night, repeat) - never on the arm - so the
    three schedulers face the same luck within a run. Comparing schedulers
    against different draws would be comparing luck.
    """
    raw: dict[str, dict[str, list[float]]] = {
        arm: {field: [] for field in FIELDS} for arm in ARMS
    }
    required_total = 0

    for night_index in range(nights):
        night = generate_night(seed + night_index, n_shots=n_shots, slack=slack,
                               required_fraction=required_fraction)
        required_total = len(night.review.required_shots)
        for repeat in range(repeats):
            costs = sample_costs(night, seed * 1_000_003 + night_index * 9_176 + repeat)
            for arm in ARMS:
                result = run_arm(arm, night, costs)
                for field in FIELDS:
                    raw[arm][field].append(float(getattr(result, field)))

    table = {arm: {f: Column.of(v) for f, v in fields.items()}
             for arm, fields in raw.items()}
    return table, nights * repeats, required_total


FIELDS = (
    "required_delivered",
    "deadline_misses",
    "cut_node_s",
    "required_delivered_at_proxy",
    "unrequired_delivered",
    "unrequired_node_s",
    "escalations",
)


def render(table: dict[str, dict[str, Column]], runs: int, required_total: int,
           nights: int, repeats: int, n_shots: int, slack: float) -> str:
    lines = [
        "CALLSHEET scheduler ablation",
        "",
        f"{nights} workloads x {repeats} cost draws = {runs} runs per arm.",
        f"{n_shots} shots per night, {required_total} required by the review, "
        f"deadline at {slack:.2f}x the required shots' final-quality work.",
        "Cells are mean +/- stdev (min-max) across runs.",
        "",
    ]

    headline = [
        ("Scheduler", 14),
        ("Required delivered", 26),
        ("Deadline misses", 26),
        ("Node-s on cut shots", 26),
    ]
    lines.append("| " + " | ".join(name.ljust(width) for name, width in headline) + " |")
    lines.append("|" + "|".join("-" * (width + 2) for _, width in headline) + "|")
    for arm in ARMS:
        row = table[arm]
        cells = [
            arm.ljust(14),
            row["required_delivered"].cell().ljust(26),
            row["deadline_misses"].cell().ljust(26),
            row["cut_node_s"].cell(1).ljust(26),
        ]
        lines.append("| " + " | ".join(cells) + " |")

    lines += ["", f"Out of {required_total} required shots per night.", "",
              "What the win costs. These are the columns where CALLSHEET can lose:", ""]

    extra = [
        ("Scheduler", 14),
        ("Delivered at proxy", 26),
        ("Other shots delivered", 26),
        ("Escalations raised", 26),
    ]
    lines.append("| " + " | ".join(name.ljust(width) for name, width in extra) + " |")
    lines.append("|" + "|".join("-" * (width + 2) for _, width in extra) + "|")
    for arm in ARMS:
        row = table[arm]
        cells = [
            arm.ljust(14),
            row["required_delivered_at_proxy"].cell().ljust(26),
            row["unrequired_delivered"].cell().ljust(26),
            row["escalations"].cell().ljust(26),
        ]
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def main() -> None:
    # ASCII, not `__doc__`: `--help` prints this, and §14's lesson is that a
    # console on a legacy codepage must not be able to kill a run on a glyph.
    parser = argparse.ArgumentParser(
        description="The CALLSHEET scheduler ablation. Prints the results table."
    )
    parser.add_argument("--nights", type=int, default=DEFAULT_NIGHTS)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--shots", type=int, default=DEFAULT_SHOTS)
    parser.add_argument("--slack", type=float, default=DEFAULT_SLACK)
    parser.add_argument("--required-fraction", type=float,
                        default=DEFAULT_REQUIRED_FRACTION,
                        help="At 1.0 every shot is required and the arms converge.")
    args = parser.parse_args()

    table, runs, required_total = collect(
        args.nights, args.repeats, args.seed, args.shots, args.slack,
        args.required_fraction,
    )
    print(render(table, runs, required_total, args.nights, args.repeats,
                 args.shots, args.slack))
    print()
    print(CAVEAT)


if __name__ == "__main__":
    main()
