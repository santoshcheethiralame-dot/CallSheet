from unittest.mock import AsyncMock, patch

import pytest

from callsheet.config import Config
from callsheet.decide import Action, Decision
from callsheet.domain import FarmState, Review, Shot
from callsheet.round import run_round
from callsheet.session import Session

NOW = 1_000_000
# Keyword args deliberately: Config's field order changed in Task 4, and
# positional construction here would bind gemini_api_key to mcp_grafana_path.
CONFIG = Config(
    grafana_url="https://x.grafana.net",
    grafana_token="glsa_abc",
    otlp_endpoint="https://o/otlp",
    otlp_auth="aGVsbG8=",
    blender_path="blender.exe",
    gemini_api_key="AIza_test",
    mcp_grafana_path="mcp-grafana",
)
SHOTS = [Shot("SH001", "a.blend", 16, [1, 2, 3], priority=90)]


@pytest.mark.asyncio
async def test_healthy_farm_makes_no_model_call():
    """The free tier survives only if a quiet round costs nothing."""
    state = FarmState(mean_frame_ms={("SH001", "final"): 1000.0}, frames_done={})
    review = Review("R", NOW + 3600, ["SH001"])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide") as decide_mock, \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    decide_mock.assert_not_called()
    assert result.decision is None
    assert result.annotation_written is False


@pytest.mark.asyncio
async def test_a_forecast_miss_triggers_a_decision_and_an_annotation():
    state = FarmState(mean_frame_ms={("SH001", "final"): 60_000.0}, frames_done={})
    review = Review("R", NOW + 10, ["SH001"])
    decision = Decision("late", [Action("SH001", "downgrade", "will miss")])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", return_value=decision) as decide_mock, \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")) as write_mock:
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    decide_mock.assert_called_once()
    write_mock.assert_awaited_once()
    assert result.decision is decision
    assert result.annotation_written is True


@pytest.mark.asyncio
async def test_a_model_failure_does_not_abort_the_round():
    """Quota exhaustion must degrade, not crash — the demo has to survive it."""
    state = FarmState(mean_frame_ms={("SH001", "final"): 60_000.0}, frames_done={})
    review = Review("R", NOW + 10, ["SH001"])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", side_effect=RuntimeError("429 quota exhausted")), \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    assert result.decision is None
    assert result.degraded_reason is not None
    assert "quota" in result.degraded_reason.lower()
    assert result.forecasts, "forecasts are still valid without the model"


@pytest.mark.asyncio
async def test_an_annotation_failure_does_not_discard_the_decision():
    """A Grafana blip must not throw away work that already succeeded."""
    state = FarmState(mean_frame_ms={("SH001", "final"): 60_000.0}, frames_done={})
    review = Review("R", NOW + 10, ["SH001"])
    decision = Decision("late", [Action("SH001", "downgrade", "will miss")])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", return_value=decision), \
         patch("callsheet.round.write_annotation", AsyncMock(side_effect=RuntimeError("grafana 503"))):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    assert result.decision is decision, "the decision survives a failed write"
    assert result.annotation_written is False
    assert "503" in result.degraded_reason


@pytest.mark.asyncio
async def test_an_insufficient_decision_is_reported_as_such_not_as_success():
    """The system must never claim to have fixed something it has not."""
    state = FarmState(mean_frame_ms={("SH001", "final"): 60_000.0})
    review = Review("R", NOW + 10, ["SH001"])
    decision = Decision("done", [Action("SH001", "downgrade", "x")])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", return_value=decision), \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    assert result.residuals
    assert result.residuals[0].closed is False
    assert result.residuals[0].shortfall_s > 0


@pytest.mark.asyncio
async def test_the_caller_supplies_the_progress_the_telemetry_still_refuses_to():
    """`parse_farm_state` deliberately never reports progress, and it still
    does not. The round takes the queue's answer from whoever holds the queue
    and merges it in, rather than teaching the telemetry layer to guess."""
    state = FarmState(mean_frame_ms={("SH001", "final"): 1000.0})
    review = Review("R", NOW + 3600, ["SH001"])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW,
                                 frames_done={"SH001": 2})

    assert result.forecasts[0].frames_remaining == 1
    assert state.frames_done == {}, \
        "the observed state is copied, not mutated - telemetry still says nothing"


@pytest.mark.asyncio
async def test_a_shot_the_queue_calls_complete_is_never_in_an_unclosed_residual():
    """The self-contradiction Phase 4 shipped, pinned shut.

    SH003's card read `in_the_can 3/3` while the call sheet beside it said SH003
    was 18s short: the cards counted PNGs on disk and the forecaster read
    `FarmState.frames_done`, which nothing ever populated. Here one dict — the
    queue's answer — goes into the round and into the board, so the two halves
    of the page are reading the same number and cannot disagree by construction.
    """
    shots = [Shot("SH001", "a.blend", 16, [1, 2, 3]),
             Shot("SH003", "c.blend", 256, [1, 2, 3])]
    review = Review("Director review", NOW + 10, ["SH001", "SH003"])
    state = FarmState(mean_frame_ms={("SH001", "final"): 60_000.0,
                                     ("SH003", "final"): 60_000.0})
    done = {"SH003": 3}     # the queue: SH003 is finished, SH001 has not started
    decision = Decision("Dropping SH001 to proxy",
                        [Action("SH001", "downgrade", "will miss")])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", return_value=decision), \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, shots, review, now_epoch_s=NOW,
                                 frames_done=done)

    board = Session().record(result, shots, review, frames_done=done,
                             now_epoch_s=NOW)

    complete = {card.shot_id for card in board.cards
                if card.frames_done >= card.frames_total}
    unclosed = {residual.shot_id for residual in board.residuals
                if not residual.closed}

    assert complete == {"SH003"}, "the card says SH003 is in the can"
    assert unclosed == {"SH001"}, "and the call sheet still reports the real gap"
    assert not complete & unclosed, \
        "no shot may be complete and short of the deadline at the same time"


@pytest.mark.asyncio
async def test_guard_rejected_actions_are_recorded_and_not_applied():
    state = FarmState(mean_frame_ms={("SH001", "final"): 60_000.0})
    review = Review("R", NOW + 10, ["SH001"])
    decision = Decision("bad", [Action("SH001", "preempt", "sacrificing the required shot")])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", return_value=decision), \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    assert result.guard_rejections, "preempting the at-risk shot must be rejected"

    # Recording the rejection is half the claim. The other half is that the
    # action never reached the queue, and only the residual can show it: SH001
    # has 3 frames at 60s, so it lands 170s past a 10s deadline. Had the preempt
    # been applied the shot would be gone from the queue and `verify` would
    # report the -1 sentinel instead — a required shot that never renders.
    assert [(r.shot_id, r.shortfall_s, r.closed) for r in result.residuals] == \
        [("SH001", 170, False)]
