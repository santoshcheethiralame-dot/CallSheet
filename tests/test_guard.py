from callsheet.decide import Action
from callsheet.forecast import Forecast
from callsheet.guard import rejected, surviving

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


def test_downgrading_the_at_risk_shot_is_allowed_from_the_front_of_the_queue():
    """The exemption is about *whose* shot it is, never about where it sits."""
    assert rejected([Action("SH001", "downgrade", "x")], FORECASTS, "SH001") == []


def test_downgrading_a_shot_behind_the_at_risk_shot_is_still_rejected():
    """The downgrade exemption is narrow: only the at-risk shot's own tier."""
    result = rejected([Action("SH003", "downgrade", "x")], FORECASTS, "SH001")
    assert len(result) == 1
    assert "behind" in result[0][1].lower()


def test_escalate_is_never_rejected():
    assert rejected([Action("SH003", "escalate", "x")], FORECASTS, "SH003") == []


def test_an_action_on_an_unknown_shot_is_rejected():
    result = rejected([Action("SH999", "preempt", "x")], FORECASTS, "SH003")
    assert len(result) == 1
    assert "unknown" in result[0][1].lower()


def test_each_action_is_judged_on_its_own_merits():
    result = rejected(
        [Action("SH002", "preempt", "cut"), Action("SH003", "preempt", "x")],
        FORECASTS,
        "SH003",
    )
    assert [action.shot_id for action, _ in result] == ["SH003"]


def test_nothing_is_rejected_for_queue_position_when_the_at_risk_shot_is_unknown():
    """With no anchor there is no position to reason about; don't invent one."""
    assert rejected([Action("SH002", "preempt", "cut")], FORECASTS, "SH404") == []


def test_surviving_returns_only_the_actions_the_guard_let_through():
    keep = Action("SH002", "preempt", "cut")
    drop = Action("SH003", "preempt", "x")
    assert surviving([keep, drop], [(drop, "the at-risk shot itself")]) == [keep]


def test_surviving_keeps_an_identical_twin_of_a_rejected_action():
    """`Action` is a frozen dataclass, so two distinct actions with the same
    fields compare equal. Filtering by equality would delete a legitimate action
    that merely looks like a rejected one."""
    blocked = Action("SH003", "preempt", "x")
    twin = Action("SH003", "preempt", "x")

    result = surviving([twin, blocked], [(blocked, "the at-risk shot itself")])

    assert len(result) == 1
    assert result[0] is twin


def test_surviving_nothing_rejected_is_the_whole_list():
    actions = [Action("SH002", "preempt", "cut")]
    assert surviving(actions, []) == actions
