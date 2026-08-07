import logging
from unittest.mock import AsyncMock, patch

import pytest

from callsheet.config import Config
from callsheet.decide import MODEL_UNAVAILABLE, QUOTA_SPENT, Action, Decision
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
    assert MODEL_UNAVAILABLE not in result.degraded_reason, \
        "the model answered; it is Grafana that did not, and the banner says so"


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


# --- Asking once ------------------------------------------------------------
#
# The free tier allows 20 model calls per day per model, measured against the
# live key: `GenerateRequestsPerDayPerProjectPerModel-FreeTier, limit: 20`. The
# timer fires every 30s and a fresh night misses on every round, so a system
# that asks on every miss is out of quota in ten minutes and 429s on camera.

DEADLINE_S = 10
"""Re-based on `now` every round, exactly as `server.run_rounds` does. This is
load-bearing for the tests below and for production: a *fixed* absolute deadline
would make the shortfall grow by 30s per round, so no two rounds would ever
share a situation and nothing would ever be reused."""

DOWNGRADE_SH001 = Decision("Dropping SH001 to proxy",
                           [Action("SH001", "downgrade", "will miss")])


def farm(**frame_ms: float) -> FarmState:
    """A farm where each named shot renders at the given ms per frame."""
    return FarmState(mean_frame_ms={(shot, "final"): rate
                                    for shot, rate in frame_ms.items()},
                     frames_done={})


async def a_night(states, shots=SHOTS, required=("SH001",),
                  decision=DOWNGRADE_SH001, session=None):
    """Drive one session through consecutive rounds, the way the timer does.

    One farm state per round, 30s apart, through a single `Session` — so the
    memory that spans rounds is the real one and not a stand-in.
    """
    session = session or Session()
    boards = []

    with patch("callsheet.round.read_farm_state",
               AsyncMock(side_effect=list(states))), \
         patch("callsheet.round.decide", return_value=decision) as decide_mock, \
         patch("callsheet.round.write_annotation",
               AsyncMock(return_value="ok")) as write_mock:
        for tick, _ in enumerate(states):
            now = NOW + tick * 30
            tonight = Review("R", now + DEADLINE_S, list(required))
            result = await run_round(CONFIG, shots, tonight, now_epoch_s=now,
                                     reuse=session.reuse)
            boards.append(session.record(result, shots, tonight,
                                         frames_done={}, now_epoch_s=now))

    return boards, decide_mock, write_mock


@pytest.mark.asyncio
async def test_an_unchanged_situation_is_not_asked_about_twice():
    """The requirement the whole quota problem reduces to.

    SH001 is 170s short in both rounds — the same shot, the same shortfall, the
    same production question. Judgement has already been passed on it, so the
    second round reuses that judgement and the day still has 19 calls left.
    """
    _, decide_mock, _ = await a_night([farm(SH001=60_000.0),
                                       farm(SH001=60_000.0)])

    assert decide_mock.call_count == 1


@pytest.mark.asyncio
async def test_a_shortfall_that_moves_past_the_bucket_is_asked_about_again():
    """170s short, then 215s short. The farm has genuinely slowed and the right
    sacrifice may no longer be the same one, so the model is asked again."""
    _, decide_mock, _ = await a_night([farm(SH001=60_000.0),
                                       farm(SH001=75_000.0)])

    assert decide_mock.call_count == 2


@pytest.mark.asyncio
async def test_a_shot_newly_at_risk_is_asked_about_again():
    """A new shot in trouble is a new question even at the same shortfall.

    Rates chosen so SH002's bucket does not move: it is 170s short in the first
    round and 172s short in the second, both bucket 17. The only thing that
    changed is that SH001 slipped from 9s to 11s against a 10s deadline and is
    now missing too — and that alone must re-open the question, because the
    plan that sacrificed something for SH002 may now be sacrificing the shot
    that needs saving.
    """
    shots = [Shot("SH001", "a.blend", 16, [1, 2, 3]),
             Shot("SH002", "b.blend", 64, [1, 2, 3])]

    _, decide_mock, _ = await a_night(
        [farm(SH001=3_000.0, SH002=57_000.0),
         farm(SH001=3_600.0, SH002=57_000.0)],
        shots=shots, required=("SH001", "SH002"),
        decision=Decision("Escalating", [Action("SH002", "escalate", "no lever left")]),
    )

    assert decide_mock.call_count == 2


@pytest.mark.asyncio
async def test_a_reused_decision_still_produces_the_same_board():
    """Reuse is not a skipped round. The guard still runs, the verifier still
    runs against the current clock, and the page is served the same call sheet
    it was served 30s ago — same instructions, same gap, same paper."""
    boards, _, _ = await a_night([farm(SH001=60_000.0), farm(SH001=60_000.0)])
    first, second = boards

    assert second.actions == first.actions
    assert second.residuals == first.residuals
    assert second.revision == first.revision == 1, \
        "nothing was amended, so no new stock was issued"


@pytest.mark.asyncio
async def test_a_reused_decision_is_not_annotated_a_second_time():
    """An annotation records the moment a decision was taken. Re-posting it
    every 30s would put forty copies of one event on the Grafana timeline and
    make the dashboard read as a system deciding over and over."""
    _, _, write_mock = await a_night([farm(SH001=60_000.0), farm(SH001=60_000.0)])

    assert write_mock.await_count == 1


@pytest.mark.asyncio
async def test_reuse_is_not_a_degrade():
    """The banner must stay clear. Not asking a question already answered is
    the system working correctly, and a page that announced it as a fallback
    would be reporting good behaviour as failure."""
    boards, _, _ = await a_night([farm(SH001=60_000.0), farm(SH001=60_000.0)])

    assert [board.degraded_reason for board in boards] == [None, None]


@pytest.mark.asyncio
async def test_a_spent_quota_reaches_the_page_as_english_and_the_log_as_itself(caplog):
    """What a viewer reads when the day's 20 calls are gone.

    The board prints `degraded_reason` verbatim, so a raw `429
    RESOURCE_EXHAUSTED` there tells an audience the product broke. It has not:
    it is scheduling by priority. The operator still needs the real thing, so
    the exception goes to the log untouched.
    """
    state = farm(SH001=60_000.0)
    review = Review("R", NOW + 10, ["SH001"])
    error = RuntimeError(
        "429 RESOURCE_EXHAUSTED. {'quotaId': "
        "'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'limit': 20}")

    with caplog.at_level(logging.WARNING), \
         patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", side_effect=error), \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    assert result.degraded_reason == QUOTA_SPENT
    assert "GenerateRequestsPerDayPerProjectPerModel-FreeTier" in caplog.text


@pytest.mark.asyncio
async def test_a_failure_that_is_not_the_quota_still_says_what_it_was():
    """Only the quota gets its own sentence, because only the quota needs one.
    Everything else is framed and then quoted: a viewer gets a readable line
    and whoever is debugging still gets the error."""
    state = farm(SH001=60_000.0)
    review = Review("R", NOW + 10, ["SH001"])

    with patch("callsheet.round.read_farm_state", AsyncMock(return_value=state)), \
         patch("callsheet.round.decide", side_effect=RuntimeError("bad key")), \
         patch("callsheet.round.write_annotation", AsyncMock(return_value="ok")):
        result = await run_round(CONFIG, SHOTS, review, now_epoch_s=NOW)

    assert result.degraded_reason == f"{MODEL_UNAVAILABLE}: bad key"
