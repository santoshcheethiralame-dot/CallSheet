import time

from callsheet.board import BoardEvent
from callsheet.decide import Action, Decision
from callsheet.domain import Review, Shot
from callsheet.forecast import Forecast
from callsheet.round import RoundResult
from callsheet.session import Session, open_night, reconcile_with_disk, situation
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


def test_the_queue_is_reconciled_with_the_frames_already_on_disk(tmp_path):
    """First boot must not re-plan a night that is already half rendered."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "SH001_0001.png").write_bytes(b"\x89PNG")
    (out / "SH001_0002.png").write_bytes(b"\x89PNG")

    session = Session(conn=open_night(SHOTS, out, tmp_path / "callsheet.db"))

    assert session.progress() == {"SH001": 2}


def test_a_frame_whose_output_is_gone_is_no_longer_finished(tmp_path):
    """The disk is the authority on whether a frame exists.

    This reverses an earlier rule — "disk seeds the queue once, the queue
    answers thereafter" — which was wrong in a way the board made obvious. With
    the frames deleted, the queue went on reporting a shot complete, and the row
    pointed its thumbnail at a file that was not there and rendered as a broken
    image. A render farm's output *is* the file; claiming the work is finished
    when its artifact is gone is the same dishonesty as claiming a deadline was
    met when it was missed.

    Nothing is lost by this. Clearing the night on purpose goes through
    `fresh_night.py`, which resets the frames and the queue together.
    """
    out = tmp_path / "out"
    out.mkdir()
    (out / "SH001_0001.png").write_bytes(b"\x89PNG")
    db = tmp_path / "callsheet.db"
    open_night(SHOTS, out, db).close()

    (out / "SH001_0001.png").unlink()
    session = Session(conn=open_night(SHOTS, out, db))

    assert session.progress() == {}, "a frame with no file is not a finished frame"


def test_a_preempted_frame_is_not_resurrected_by_its_file_appearing(tmp_path):
    """Preemption is a production decision, not an observation of the disk."""
    import sqlite3

    from callsheet import queue as q

    out = tmp_path / "out"
    out.mkdir()
    conn = sqlite3.connect(":memory:")
    q.init_db(conn)
    q.enqueue_manifest(conn, SHOTS)
    conn.execute("UPDATE jobs SET state = 'preempted' WHERE shot_id = 'SH001'")
    conn.commit()

    (out / "SH001_0001.png").write_bytes(b"\x89PNG")
    assert reconcile_with_disk(conn, SHOTS, out) == []
    assert q.frames_done(conn) == {}


def test_a_session_with_no_queue_behind_it_reports_no_progress():
    """The no-credentials path: no live round, so no queue, and the server
    falls back to counting the disk rather than being handed a wrong number."""
    assert Session().progress() == {}


def test_a_session_has_no_board_until_a_round_has_run():
    """The server needs this to know whether to fall back to the disk board."""
    assert Session().board is None


# --- The situation key ------------------------------------------------------
#
# `amendment` decides two decisions are the same after the model has already
# been paid for. These decide whether the *question* is the same, before it is
# asked. SH003 in BEHIND lands at NOW+118 against a NOW+30 deadline: 88s short,
# bucket 8.


def a_miss(shot_id, finishes_at):
    return Forecast(shot_id, 3, 0.0, finishes_at, True, "observed")


def test_a_second_of_drift_is_not_a_new_situation():
    """The raw shortfall never repeats exactly — telemetry means move with
    every frame and the forecast rounds up. Keyed on the exact number, nothing
    would ever match and the bucket would be doing no work at all."""
    drifted = [QUIET[0], QUIET[1], a_miss("SH003", NOW + 119)]

    assert situation(drifted, REVIEW) == situation(BEHIND, REVIEW)


def test_a_shortfall_that_moves_a_bucket_is_a_new_situation():
    """88s short, then 100s short. Twelve seconds is a frame and a half here,
    and the plan that covered the first gap may not cover the second."""
    worse = [QUIET[0], QUIET[1], a_miss("SH003", NOW + 130)]

    assert situation(worse, REVIEW) != situation(BEHIND, REVIEW)


def test_a_newly_missing_shot_is_a_new_situation_at_the_same_shortfall():
    """SH003 is 88s short in both. The key still differs, because which shots
    are in trouble is half the question and not a detail of how late they are."""
    both = [a_miss("SH001", NOW + 40), QUIET[1], BEHIND[2]]

    assert situation(both, REVIEW) != situation(BEHIND, REVIEW)


def test_a_night_with_nothing_missing_has_nothing_to_reuse():
    """A quiet round never reaches the model, so there is no answer standing
    for it — and an empty key must not be allowed to match another empty one
    and hand back a decision made about a farm that was in trouble."""
    session = Session()
    record(session, a_round(decision=PREEMPT_SH002))

    assert session.reuse(QUIET, REVIEW) is None


def test_the_same_situation_hands_back_the_decision_already_made():
    session = Session()
    record(session, a_round(BEHIND, decision=PREEMPT_SH002))

    assert session.reuse(BEHIND, REVIEW) is PREEMPT_SH002


def test_a_round_that_never_got_an_answer_leaves_nothing_to_reuse():
    """The quota rolls over at Pacific midnight and a night running through it
    should come back on its own, so a situation whose call failed is asked
    again next tick. Retrying costs nothing while the quota is spent: a request
    refused for quota is refused before it is counted."""
    session = Session()
    record(session, a_round(BEHIND, degraded_reason="429 RESOURCE_EXHAUSTED"))

    assert session.reuse(BEHIND, REVIEW) is None


def test_events_survive_across_rounds():
    session = Session()
    session.events.append(BoardEvent(NOW - 1, "SH001 frame 3 rendered"))

    board = record(session, a_round(decision=PREEMPT_SH002))

    assert board.events[0].text == "SH001 frame 3 rendered"


def test_a_frame_that_lands_mid_night_is_picked_up_without_a_restart(tmp_path):
    """Reconciling only at boot left the board frozen while Blender worked."""
    import sqlite3

    from callsheet.domain import Shot
    from callsheet.session import Session, reconcile_with_disk
    from callsheet import queue as q

    shots = [Shot("SH001", "a.blend", 16, [1, 2])]
    out = tmp_path / "out"
    out.mkdir()

    conn = sqlite3.connect(":memory:")
    q.init_db(conn)
    q.enqueue_manifest(conn, shots)
    session = Session(conn=conn)

    assert session.catch_up(shots, out) == [], "nothing has rendered yet"

    (out / "SH001_0001.png").write_bytes(b"png")
    assert session.catch_up(shots, out) == [("SH001", 1)]
    assert session.progress() == {"SH001": 1}

    # Idempotent: the same frame is not news twice.
    assert session.catch_up(shots, out) == []

    (out / "SH001_0002.png").write_bytes(b"png")
    assert session.catch_up(shots, out) == [("SH001", 2)]
    assert session.progress() == {"SH001": 2}


def test_catch_up_without_a_queue_is_a_no_op():
    from pathlib import Path as P

    from callsheet.session import Session

    assert Session().catch_up([], P("nowhere")) == []
