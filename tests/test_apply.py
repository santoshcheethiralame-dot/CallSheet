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
    assert [shot.quality for shot in SHOTS] == ["final", "final", "final"]


def test_downgrading_an_already_proxy_shot_leaves_it_alone():
    shots = [Shot("SH003", "c.blend", 256, [1], quality="proxy")]
    assert apply_actions(shots, [Action("SH003", "downgrade", "x")]) == shots


def test_preempt_wins_over_downgrade_for_the_same_shot():
    """A shot that is gone cannot also be rendered cheaply."""
    result = apply_actions(
        SHOTS, [Action("SH002", "downgrade", "x"), Action("SH002", "preempt", "x")]
    )
    assert [shot.id for shot in result] == ["SH001", "SH003"]


def test_no_actions_returns_the_queue_unchanged():
    assert apply_actions(SHOTS, []) == SHOTS
