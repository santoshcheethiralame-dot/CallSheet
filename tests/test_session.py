import time

from callsheet.board import BoardEvent
from callsheet.decide import Action, Decision
from callsheet.domain import Review, Shot
from callsheet.forecast import Forecast
from callsheet.round import RoundResult
from callsheet.session import Session
from callsheet.verify import Residual

NOW = 1_000_000
SHOTS = [
    Shot("SH001", "a.blend", 16, [1, 2, 3]),
    Shot("SH002", "b.blend", 64, [1, 2, 3]),
    Shot("SH003", "c.blend", 256, [1, 2, 3]),
]
REVIEW = Review("Director review", NOW + 30, ["SH001", "SH003"])

QUIET = [
    Forecast("SH001", 3, 15_441.0, NOW + 16, False, "observed"),
    Forecast("SH002", 3, 22_023.0, NOW + 38, False, "observed"),
    Forecast("SH003", 3, 80_445.0, NOW + 118, False, "observed"),
]
BEHIND = [
    QUIET[0],
    QUIET[1],
    Forecast("SH003", 3, 80_445.0, NOW + 118, True, "observed"),
]

PREEMPT_SH002 = Decision(
    "Striking SH002 to protect the review",
    [Action("SH002", "preempt", "already cut")],
)


def a_round(forecasts=BEHIND, decision=None, **kwargs):
    """A RoundResult in the shape `run_round` returns."""
    return RoundResult(list(forecasts), decision, decision is not None, **kwargs)


def record(session, result, review=REVIEW, now=NOW, frames_done=None):
    return session.record(result, SHOTS, review,
                          frames_done=frames_done or {}, now_epoch_s=now)


def test_a_round_with_no_miss_leaves_the_revision_alone():
    """The revision is not a clock. Nothing happened, so nothing was reissued."""
    session = Session()

    board = record(session, a_round(QUIET))

    assert session.revision == 0
    assert board.revision == 0
    assert board.stock == "white"


def test_a_decision_issues_a_new_revision():
    session = Session()

    board = record(session, a_round(decision=PREEMPT_SH002))

    assert board.revision == 1
    assert board.stock == "blue"


def test_the_same_decision_does_not_issue_a_second_revision():
    """The timer re-runs long before the farm changes. A call sheet reissued
    every tick with the same content on it is a clock, not an amendment."""
    session = Session()

    record(session, a_round(decision=PREEMPT_SH002))
    board = record(session, a_round(decision=PREEMPT_SH002), now=NOW + 30)

    assert board.revision == 1
    assert [e.text for e in board.events].count("Revision 1 issued - blue") == 1


def test_a_different_decision_issues_another_revision():
    session = Session()

    record(session, a_round(decision=PREEMPT_SH002))
    board = record(session, a_round(decision=Decision(
        "Dropping SH003 to proxy", [Action("SH003", "downgrade", "review only")],
    )), now=NOW + 30)

    assert board.revision == 2
    assert board.stock == "pink"


def test_a_reworded_summary_is_not_a_new_revision():
    """Observed live: the same plan came back on the next round as "to clear the
    queue ahead of SH003" instead of "to allow SH003 to finish". Same
    instruction, new sentence. Reissuing on that puts the revision back on the
    timer, which is the one thing it must never be."""
    session = Session()

    record(session, a_round(decision=PREEMPT_SH002))
    board = record(session, a_round(decision=Decision(
        "Striking SH002 so the director sees SH003",
        list(PREEMPT_SH002.actions),
    )), now=NOW + 30)

    assert board.revision == 1
    assert board.summary == "Striking SH002 so the director sees SH003", \
        "the paper still shows the latest wording; it is just not a new revision"


def test_the_revision_follows_what_the_guard_allowed_not_what_was_proposed():
    """Same decision, but this time the guard threw the action out. What the
    crew is being told to do has changed, so the call sheet has changed."""
    session = Session()
    record(session, a_round(decision=PREEMPT_SH002))

    board = record(session, a_round(
        decision=PREEMPT_SH002,
        guard_rejections=[(PREEMPT_SH002.actions[0], "behind SH003 in the queue")],
    ), now=NOW + 30)

    assert board.revision == 2
    assert board.actions == []


def test_events_accumulate_newest_last():
    session = Session()

    record(session, a_round(decision=PREEMPT_SH002))
    board = record(session, a_round(decision=Decision("Escalating", [])), now=NOW + 30)

    texts = [event.text for event in board.events]
    assert texts.index("Revision 1 issued - blue") < texts.index("Revision 2 issued - pink")
    assert board.events[-1].at_epoch_s == NOW + 30


def test_events_are_capped_so_a_long_night_cannot_grow_unbounded():
    session = Session(max_events=3)

    for index in range(10):
        record(session, a_round(decision=Decision(
            f"Plan {index}", [Action(f"SH{index:03d}", "preempt", "cut")],
        )), now=NOW + index)

    assert len(session.events) == 3
    assert session.events[-1].text == "Revision 10 issued - goldenrod"


def test_a_missed_review_is_reported_the_way_a_coordinator_would_say_it():
    session = Session()

    board = record(session, a_round(BEHIND))

    when = time.strftime("%H:%M", time.localtime(REVIEW.deadline_epoch_s))
    assert board.events[0].text == f"SH003 will not make the {when} review"


def test_a_shot_that_is_still_behind_is_not_announced_again():
    """It is the same bad news. The feed reports changes, not the clock."""
    session = Session()

    record(session, a_round(BEHIND))
    record(session, a_round(BEHIND), now=NOW + 30)

    assert sum("will not make" in e.text for e in session.events) == 1


def test_a_degraded_round_says_the_system_is_working_in_a_lesser_mode():
    session = Session()

    board = record(session, a_round(degraded_reason="503 UNAVAILABLE"))

    assert "Scheduling by priority - the model is unavailable" in [
        e.text for e in board.events
    ]


def test_a_degraded_round_does_not_repeat_itself_every_tick():
    session = Session()

    record(session, a_round(degraded_reason="503 UNAVAILABLE"))
    record(session, a_round(degraded_reason="503 UNAVAILABLE"), now=NOW + 30)

    assert sum("Scheduling by priority" in e.text for e in session.events) == 1


def test_a_degraded_round_still_shows_the_forecast():
    """Degraded means no plan, not no information. The cards must still be
    telling the truth about the farm while the model is out."""
    session = Session()

    board = record(session, a_round(BEHIND, degraded_reason="503 UNAVAILABLE"))

    assert board.degraded_reason == "503 UNAVAILABLE"
    assert next(c for c in board.cards if c.shot_id == "SH003").state == "at_risk"
    assert next(c for c in board.cards if c.shot_id == "SH001").eta_s == 16


def test_a_degraded_round_does_not_issue_a_revision():
    session = Session()

    board = record(session, a_round(BEHIND, degraded_reason="503 UNAVAILABLE"))

    assert board.revision == 0


def test_the_board_carries_the_residuals_of_the_round():
    session = Session()

    board = record(session, a_round(
        decision=PREEMPT_SH002,
        residuals=[Residual("SH003", 65, False)],
    ))

    assert board.residuals == [Residual("SH003", 65, False)]


def test_frames_on_disk_reach_the_cards():
    session = Session()

    board = record(session, a_round(QUIET), frames_done={"SH001": 2})

    card = next(c for c in board.cards if c.shot_id == "SH001")
    assert card.frames_done == 2
    assert card.thumbnail == "/frames/SH001_0002.png"


def test_a_session_has_no_board_until_a_round_has_run():
    """The server needs this to know whether to fall back to the disk board."""
    assert Session().board is None


def test_events_survive_across_rounds():
    session = Session()
    session.events.append(BoardEvent(NOW - 1, "SH001 frame 3 rendered"))

    board = record(session, a_round(decision=PREEMPT_SH002))

    assert board.events[0].text == "SH001 frame 3 rendered"
