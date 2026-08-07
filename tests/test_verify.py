from callsheet.decide import Action
from callsheet.domain import FarmState, Review, Shot
from callsheet.verify import verify

NOW = 1_000_000
SHOTS = [
    Shot("SH001", "a.blend", 16, [1, 2, 3]),
    Shot("SH002", "b.blend", 64, [1, 2, 3]),
    Shot("SH003", "c.blend", 256, [1, 2, 3]),
]
STATE = FarmState(mean_frame_ms={
    ("SH001", "final"): 5147.0, ("SH001", "proxy"): 1930.0,
    ("SH002", "final"): 7341.0, ("SH002", "proxy"): 2750.0,
    ("SH003", "final"): 26815.0, ("SH003", "proxy"): 7020.0,
})


def test_an_insufficient_action_reports_the_residual_shortfall():
    """The Phase 2 case: preempting SH002 helps, but nowhere near enough.

    By hand: SH001 runs 3 x 5147ms = 15441ms, then SH003 3 x 26815ms, landing at
    95886ms = 96s against a 30s deadline. 66s short.
    """
    review = Review("R", NOW + 30, ["SH003"])

    residuals = verify(SHOTS, [Action("SH002", "preempt", "cut")], review, STATE, NOW)

    sh003 = next(r for r in residuals if r.shot_id == "SH003")
    assert sh003.closed is False
    assert sh003.shortfall_s == 66


def test_a_sufficient_action_closes_the_gap():
    review = Review("R", NOW + 30, ["SH003"])

    residuals = verify(
        SHOTS,
        [Action("SH001", "preempt", "x"), Action("SH002", "preempt", "x"),
         Action("SH003", "downgrade", "x")],
        review, STATE, NOW,
    )

    sh003 = next(r for r in residuals if r.shot_id == "SH003")
    assert sh003.closed is True
    assert sh003.shortfall_s == 0


def test_no_actions_reports_the_original_shortfall():
    review = Review("R", NOW + 30, ["SH003"])
    residuals = verify(SHOTS, [], review, STATE, NOW)
    assert next(r for r in residuals if r.shot_id == "SH003").shortfall_s > 60


def test_only_required_shots_appear_in_the_residual():
    review = Review("R", NOW + 30, ["SH003"])
    residuals = verify(SHOTS, [], review, STATE, NOW)
    assert {r.shot_id for r in residuals} == {"SH003"}


def test_preempting_a_required_shot_is_never_reported_as_closed():
    """The gap is not closed by deleting the thing the review asked for."""
    review = Review("R", NOW + 30, ["SH003"])

    residuals = verify(SHOTS, [Action("SH003", "preempt", "x")], review, STATE, NOW)

    assert [(r.shot_id, r.closed) for r in residuals] == [("SH003", False)]
    assert residuals[0].shortfall_s < 0


def test_the_queue_the_verifier_was_given_is_left_alone():
    review = Review("R", NOW + 30, ["SH003"])
    verify(SHOTS, [Action("SH002", "preempt", "x")], review, STATE, NOW)
    assert [shot.id for shot in SHOTS] == ["SH001", "SH002", "SH003"]
