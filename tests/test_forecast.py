import pytest

from callsheet.domain import FarmState, Review, Shot
from callsheet.forecast import forecast_all, misses

NOW = 1_000_000


def _shot(shot_id, frames=3, priority=50, is_cut=False):
    return Shot(id=shot_id, scene=f"{shot_id}.blend", samples=64,
                frames=list(range(1, frames + 1)), priority=priority, is_cut=is_cut)


def test_uses_observed_mean_for_shots_with_history():
    shots = [_shot("SH001", frames=3)]
    state = FarmState(mean_frame_ms={("SH001", "final"): 5000.0}, frames_done={"SH001": 0})
    review = Review("R", NOW + 3600, ["SH001"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.frames_remaining == 3
    assert forecast.predicted_ms == 15000.0
    assert forecast.finishes_at_epoch_s == NOW + 15
    assert forecast.misses_deadline is False


def test_frames_already_done_are_not_re_forecast():
    shots = [_shot("SH001", frames=3)]
    state = FarmState(mean_frame_ms={("SH001", "final"): 5000.0}, frames_done={"SH001": 2})
    review = Review("R", NOW + 3600, ["SH001"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.frames_remaining == 1
    assert forecast.predicted_ms == 5000.0


def test_a_re_rendered_shot_never_reports_negative_frames_remaining():
    """frames_done can exceed the manifest when a shot is re-rendered."""
    shots = [_shot("SH001", frames=3)]
    state = FarmState(mean_frame_ms={("SH001", "final"): 5000.0}, frames_done={"SH001": 7})
    review = Review("R", NOW + 3600, ["SH001"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.frames_remaining == 0
    assert forecast.predicted_ms == 0.0


def test_shot_with_no_history_uses_the_fallback():
    shots = [_shot("SH999", frames=2)]
    state = FarmState()
    review = Review("R", NOW + 3600, ["SH999"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW, fallback_frame_ms=8000.0)[0]

    assert forecast.predicted_ms == 16000.0


def test_a_measured_forecast_says_so():
    """A judge must be able to tell a measurement from a guess."""
    shots = [_shot("SH001", frames=3)]
    state = FarmState(mean_frame_ms={("SH001", "final"): 5000.0}, frames_done={})
    review = Review("R", NOW + 3600, ["SH001"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.estimate_source == "observed"


def test_a_guessed_forecast_says_so_too():
    """Silently substituting an 8s guess would let a fabrication read as a measurement."""
    shots = [_shot("SH999", frames=2)]
    state = FarmState()
    review = Review("R", NOW + 3600, ["SH999"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.estimate_source == "fallback"


def test_a_shot_that_cannot_finish_in_time_is_flagged():
    shots = [_shot("SH003", frames=3)]
    state = FarmState(mean_frame_ms={("SH003", "final"): 26000.0}, frames_done={"SH003": 0})
    review = Review("R", NOW + 60, ["SH003"])   # 78s of work, 60s of runway

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.misses_deadline is True
    assert misses([forecast]) == [forecast]


def test_a_sub_second_overrun_still_counts_as_a_miss():
    """Truncating the ETA to whole seconds must not hide a shot that is late."""
    shots = [_shot("SH005", frames=1)]
    state = FarmState(mean_frame_ms={("SH005", "final"): 60_500.0}, frames_done={})
    review = Review("R", NOW + 60, ["SH005"])   # 60.5s of work, 60s of runway

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.misses_deadline is True


def test_shots_are_forecast_sequentially_because_the_farm_is_one_queue():
    """Two shots share the farm, so the second starts after the first finishes."""
    shots = [_shot("SH001", frames=1), _shot("SH002", frames=1)]
    state = FarmState(mean_frame_ms={("SH001", "final"): 5000.0, ("SH002", "final"): 7000.0}, frames_done={})
    review = Review("R", NOW + 3600, ["SH001", "SH002"])

    first, second = forecast_all(shots, review, state, now_epoch_s=NOW)

    assert first.finishes_at_epoch_s == NOW + 5
    assert second.finishes_at_epoch_s == NOW + 12


def test_a_finished_shot_does_not_queue_behind_unfinished_work():
    """A shot with nothing left to render is done, whatever is ahead of it.

    Charging it the wait in front made a completed shot report a deadline miss,
    which is how the board came to show `in_the_can 3/3` on a card and a
    shortfall for the same shot on the call sheet beside it.
    """
    shots = [_shot("SH001", frames=3), _shot("SH003", frames=3)]
    state = FarmState(mean_frame_ms={("SH001", "final"): 60_000.0,
                                     ("SH003", "final"): 60_000.0},
                      frames_done={"SH003": 3})
    review = Review("R", NOW + 10, ["SH001", "SH003"])

    behind, finished = forecast_all(shots, review, state, now_epoch_s=NOW)

    assert behind.misses_deadline is True, "SH001 really is 180s of work"
    assert finished.frames_remaining == 0
    assert finished.finishes_at_epoch_s == NOW
    assert finished.misses_deadline is False


def test_queue_order_changes_who_misses():
    """Order is load-bearing: whichever shot is queued second absorbs the wait."""
    slow = _shot("SH001", frames=1)
    quick = _shot("SH002", frames=1)
    state = FarmState(mean_frame_ms={("SH001", "final"): 40_000.0, ("SH002", "final"): 40_000.0}, frames_done={})
    review = Review("R", NOW + 60, ["SH001", "SH002"])

    slow_first = forecast_all([slow, quick], review, state, now_epoch_s=NOW)
    quick_first = forecast_all([quick, slow], review, state, now_epoch_s=NOW)

    assert [f.shot_id for f in misses(slow_first)] == ["SH002"]
    assert [f.shot_id for f in misses(quick_first)] == ["SH001"]


def test_shots_not_required_by_the_review_are_still_forecast_but_never_miss():
    shots = [_shot("SH004", frames=1)]
    state = FarmState(mean_frame_ms={("SH004", "final"): 99_000.0}, frames_done={})
    review = Review("R", NOW + 1, [])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.misses_deadline is False, "only required shots can miss a review"


def test_a_downgraded_shot_is_forecast_at_its_measured_proxy_rate():
    shots = [Shot("SH003", "c.blend", 256, [1, 2, 3], quality="proxy")]
    state = FarmState(mean_frame_ms={
        ("SH003", "final"): 26815.0,
        ("SH003", "proxy"): 7020.0,
    })
    review = Review("R", NOW + 3600, ["SH003"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.predicted_ms == pytest.approx(21060.0)
    assert forecast.estimate_source == "observed"


def test_a_quality_with_no_history_falls_back_to_final_and_says_so():
    shots = [Shot("SH003", "c.blend", 256, [1], quality="proxy")]
    state = FarmState(mean_frame_ms={("SH003", "final"): 26815.0})
    review = Review("R", NOW + 3600, ["SH003"])

    forecast = forecast_all(shots, review, state, now_epoch_s=NOW)[0]

    assert forecast.predicted_ms == pytest.approx(26815.0)
    assert forecast.estimate_source == "fallback", (
        "using the final-quality rate for a proxy render is a guess, not a measurement"
    )
