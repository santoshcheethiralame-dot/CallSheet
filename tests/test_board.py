from callsheet.board import build_board, revision_stock
from callsheet.decide import Action, Decision
from callsheet.domain import Review, Shot
from callsheet.forecast import Forecast
from callsheet.verify import Residual

NOW = 1_000_000
SHOTS = [
    Shot("SH001", "a.blend", 16, [1, 2, 3]),
    Shot("SH002", "b.blend", 64, [1, 2, 3]),
    Shot("SH003", "c.blend", 256, [1, 2, 3]),
]
REVIEW = Review("Director review", NOW + 30, ["SH001", "SH003"])
FORECASTS = [
    Forecast("SH001", 3, 15_441.0, NOW + 16, False, "observed"),
    Forecast("SH002", 3, 22_023.0, NOW + 38, False, "observed"),
    Forecast("SH003", 3, 80_445.0, NOW + 118, True, "observed"),
]


def test_revision_stock_follows_the_production_convention():
    assert revision_stock(0) == "white"
    assert revision_stock(1) == "blue"
    assert revision_stock(2) == "pink"
    assert revision_stock(3) == "goldenrod"


def test_revision_stock_stays_goldenrod_past_the_third_revision():
    """There is no paper worse than goldenrod. The scale ends there."""
    assert revision_stock(9) == "goldenrod"


def test_a_missing_shot_is_marked_at_risk():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH003").state == "at_risk"


def test_a_preempted_shot_is_marked_struck():
    decision = Decision("s", [Action("SH002", "preempt", "not required")])
    board = build_board(SHOTS, REVIEW, FORECASTS, decision,
                        applied=decision.actions, rejections=[], revision=1,
                        frames_done={}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH002").state == "struck"


def test_a_rejected_preempt_does_not_strike_the_shot():
    """The board must agree with the annotation about what was actually done."""
    action = Action("SH002", "preempt", "x")
    decision = Decision("s", [action])
    board = build_board(SHOTS, REVIEW, FORECASTS, decision,
                        applied=[], rejections=[(action, "behind")], revision=1,
                        frames_done={}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH002").state != "struck"


def test_a_fully_rendered_shot_is_in_the_can():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={"SH001": 3}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH001").state == "in_the_can"


def test_a_partially_rendered_shot_is_rendering():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={"SH001": 1}, events=[], now_epoch_s=NOW)
    card = next(c for c in board.cards if c.shot_id == "SH001")
    assert card.state == "rendering"
    assert card.frames_done == 1
    assert card.frames_total == 3


def test_the_thumbnail_points_at_the_latest_rendered_frame():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={"SH001": 2}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH001").thumbnail == "/frames/SH001_0002.png"


def test_a_shot_with_no_rendered_frames_has_no_thumbnail():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH001").thumbnail is None


def test_a_struck_shot_stays_struck_while_it_is_still_draining():
    """Precedence, not luck: SH002 is half-rendered and would otherwise read as
    `rendering`. A struck shot is nobody's problem any more, so struck wins."""
    action = Action("SH002", "preempt", "cut")
    board = build_board(SHOTS, REVIEW, FORECASTS, Decision("s", [action]),
                        applied=[action], rejections=[], revision=1,
                        frames_done={"SH002": 1}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH002").state == "struck"


def test_a_shot_that_is_rendering_and_will_still_miss_reads_at_risk():
    """The other half of the precedence: work in progress that will still be
    late is exactly what a coordinator needs to see, so at_risk beats
    rendering."""
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={"SH003": 1}, events=[], now_epoch_s=NOW)
    card = next(c for c in board.cards if c.shot_id == "SH003")
    assert card.state == "at_risk"
    assert card.frames_done == 1


def test_a_downgraded_shot_is_not_struck():
    """Only a preempt strikes a shot. A downgrade still renders."""
    action = Action("SH002", "downgrade", "cheaper")
    board = build_board(SHOTS, REVIEW, FORECASTS, Decision("s", [action]),
                        applied=[action], rejections=[], revision=1,
                        frames_done={}, events=[], now_epoch_s=NOW)
    card = next(c for c in board.cards if c.shot_id == "SH002")
    assert card.state != "struck"
    assert card.quality == "proxy", "the card must show the tier it will render at"


def test_the_card_carries_the_forecast_it_was_given():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={}, events=[], now_epoch_s=NOW)
    card = next(c for c in board.cards if c.shot_id == "SH001")
    assert card.eta_s == 16
    assert card.estimate_source == "observed"


def test_the_board_carries_the_paper_stock_for_its_revision():
    """The page must not keep a second copy of the revision table."""
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=2,
                        frames_done={}, events=[], now_epoch_s=NOW)
    assert board.revision == 2
    assert board.stock == "pink"


def test_the_board_reports_the_review_and_the_residuals_it_was_given():
    residuals = [Residual("SH003", 66, False)]
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [],
                        residuals=residuals, revision=0, frames_done={},
                        events=[], now_epoch_s=NOW)
    assert board.review_name == "Director review"
    assert board.deadline_epoch_s == NOW + 30
    assert board.residuals == residuals
