"""Applies decision actions to a shot list. Pure: returns a new list.

Pure and snapshot-based on purpose. The queue this models is a SQLite table, but
the arithmetic that decides whether a plan works must be replayable without a
database, a clock or a network, and it must be possible to run it against a
hypothetical queue that will never be committed.
"""

from __future__ import annotations

import dataclasses

from callsheet.decide import Action
from callsheet.domain import Shot


def apply_actions(shots: list[Shot], actions: list[Action]) -> list[Shot]:
    """The queue as it would be if the plan were carried out.

    `escalate` is deliberately inert: it summons a human, it does not change any
    render. Preempt beats downgrade for the same shot — a shot that has been cut
    cannot also be rendered cheaply.
    """
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
