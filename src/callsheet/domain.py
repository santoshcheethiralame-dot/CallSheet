"""The production objects. No I/O beyond reading its own JSON files."""

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

    mean_frame_ms: dict[tuple[str, str], float] = field(default_factory=dict)
    """Observed mean frame duration keyed by `(shot_id, quality)`.

    Keyed by both because a proxy frame and a final frame of the same shot are
    different amounts of work — measurably so — and a scheduler that downgrades
    a shot needs the rate for the tier it is actually going to render.
    """

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
