import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from callsheet.config import Config
from callsheet.decide import build_prompt, decide, parse_decision
from callsheet.domain import Review, Shot
from callsheet.forecast import Forecast

NOW = 1_000_000

CONFIG = Config(
    grafana_url="https://x.grafana.net",
    grafana_token="glsa_abc",
    otlp_endpoint="https://o/otlp",
    otlp_auth="aGVsbG8=",
    blender_path="blender.exe",
    gemini_api_key="AIza_test",
    mcp_grafana_path="mcp-grafana",
)

GOOD_JSON = '{"summary": "Downgrading SH001.", "actions": [' \
            '{"shot_id": "SH001", "action": "downgrade", "reason": "late"}]}'
UNKNOWN_ACTION_JSON = '{"summary": "x", "actions": [' \
                      '{"shot_id": "SH001", "action": "delete_everything", "reason": "y"}]}'

SHOTS = [
    Shot("SH001", "a.blend", 16, [1, 2, 3], priority=90, quality="final", is_cut=False),
    Shot("SH002", "b.blend", 64, [1, 2, 3], priority=10, quality="final", is_cut=True),
]
REVIEW = Review("Director review", NOW + 60, ["SH001"])
FORECASTS = [
    Forecast("SH001", 3, 90_000.0, NOW + 90, True, "observed"),
    Forecast("SH002", 3, 21_000.0, NOW + 111, False, "observed"),
]


def test_prompt_states_the_deadline_and_the_missing_shot():
    prompt = build_prompt(SHOTS, REVIEW, FORECASTS)
    assert "SH001" in prompt
    assert "Director review" in prompt
    assert "30" in prompt, "the 30s shortfall must be stated, not left to be computed"


def test_prompt_marks_cut_shots_so_the_model_can_prefer_sacrificing_them():
    prompt = build_prompt(SHOTS, REVIEW, FORECASTS)
    assert "SH002" in prompt
    assert "cut" in prompt.lower()


def test_prompt_states_the_queue_position_of_every_shot():
    """The farm is one serial queue, and the model cannot see that from ETAs alone.

    Asked to save SH001 — which is *first* in the queue — Gemini proposed
    preempting SH002 "to free up farm resources". SH002 renders after SH001, so
    the sacrifice recovers nothing. The prompt has to say what the render order
    is before a judgement about it can be sound.
    """
    prompt = build_prompt(SHOTS, REVIEW, FORECASTS)
    rows = [line for line in prompt.splitlines() if "frames left" in line]

    assert rows[0].startswith("1. SH001:")
    assert rows[1].startswith("2. SH002:")
    assert "one shot at a time" in prompt.lower()
    assert "queue" in prompt.lower()


def test_system_says_a_sacrifice_only_helps_shots_behind_it():
    from callsheet.decide import SYSTEM

    lowered = SYSTEM.lower()
    assert "after it in the queue" in lowered
    assert "achieves nothing" in lowered


def test_prompt_flags_a_guessed_estimate_so_the_model_can_discount_it():
    """A judgement made on an 8-second invention deserves to know it is one."""
    guessed = [
        Forecast("SH001", 3, 24_000.0, NOW + 90, True, "fallback"),
        Forecast("SH002", 3, 21_000.0, NOW + 111, False, "observed"),
    ]
    prompt = build_prompt(SHOTS, REVIEW, guessed)
    assert "no telemetry" in prompt.lower()


def test_nothing_sent_to_the_model_asks_it_to_do_arithmetic():
    """Covers the system instruction too, not just the data block.

    An earlier version asserted only on build_prompt, which is the half that was
    never going to contain a calculation request anyway.
    """
    from callsheet.decide import SYSTEM

    everything = (SYSTEM + build_prompt(SHOTS, REVIEW, FORECASTS)).lower()
    for banned in ("calculate", "compute", "work out", "estimate how long"):
        assert banned not in everything, f"{banned!r} reaches the model"


def test_parse_decision_reads_structured_actions():
    decision = parse_decision(
        '{"summary": "Preempting the cut shot.",'
        ' "actions": [{"shot_id": "SH002", "action": "preempt", "reason": "already cut"}]}'
    )
    assert decision.summary == "Preempting the cut shot."
    assert decision.actions[0].shot_id == "SH002"
    assert decision.actions[0].action == "preempt"


def test_parse_decision_rejects_an_unknown_action():
    with pytest.raises(ValueError, match="unknown action"):
        parse_decision('{"summary": "x", "actions": [{"shot_id": "SH002", '
                       '"action": "delete_everything", "reason": "y"}]}')


def test_parse_decision_survives_a_fenced_code_block():
    """Models wrap JSON in ``` fences often enough that this must not be fatal."""
    decision = parse_decision('```json\n{"summary": "s", "actions": []}\n```')
    assert decision.summary == "s"


def _client(*outcomes):
    """A stub genai client whose generate_content plays a scripted sequence.

    Each outcome is either a raw response body or an exception to raise.
    """
    client = MagicMock()
    client.models.generate_content.side_effect = [
        outcome if isinstance(outcome, BaseException) else SimpleNamespace(text=outcome)
        for outcome in outcomes
    ]
    return client


def _calls(client):
    return client.models.generate_content.call_count


def _decide(client):
    """Run `decide` against a stub client with the backoff sleep neutralised."""
    with patch("callsheet.decide.genai.Client", return_value=client), \
         patch("callsheet.decide.time.sleep") as sleep:
        return decide(CONFIG, SHOTS, REVIEW, FORECASTS), sleep


def test_a_transient_upstream_failure_is_retried():
    """The observed failure: four 503s in one session. A degrade is correct, but
    on a recorded demo it means the product never appears on screen."""
    client = _client(RuntimeError("503 UNAVAILABLE: the model is overloaded"), GOOD_JSON)

    decision, _ = _decide(client)

    assert _calls(client) == 2
    assert decision.summary == "Downgrading SH001."


def test_a_rate_limit_is_retried_too():
    client = _client(RuntimeError("429 RESOURCE_EXHAUSTED"), GOOD_JSON)
    decision, _ = _decide(client)
    assert _calls(client) == 2
    assert decision.actions[0].shot_id == "SH001"


def test_retries_back_off_instead_of_hammering_the_api():
    client = _client(RuntimeError("503 UNAVAILABLE"),
                     RuntimeError("503 UNAVAILABLE"), GOOD_JSON)

    _, sleep = _decide(client)

    waits = [call.args[0] for call in sleep.call_args_list]
    assert len(waits) == 2
    assert waits[1] > waits[0], "the wait must grow between attempts"
    assert sum(waits) < 5, "a demo cannot stall; the backoff stays short"


def test_a_malformed_reply_is_not_retried():
    """A model that returned nonsense will return nonsense again. Retrying it
    burns free-tier quota and delays the degrade path for nothing."""
    client = _client(UNKNOWN_ACTION_JSON, GOOD_JSON)

    with pytest.raises(ValueError, match="unknown action"):
        _decide(client)

    assert _calls(client) == 1


def test_an_auth_failure_is_not_retried():
    client = _client(RuntimeError("401 UNAUTHENTICATED: API key not valid"), GOOD_JSON)

    with pytest.raises(RuntimeError, match="401"):
        _decide(client)

    assert _calls(client) == 1


def test_retries_are_bounded_and_the_last_failure_is_raised():
    """Retrying must not mask a real outage — `run_round` still has to degrade."""
    client = _client(*[RuntimeError("503 UNAVAILABLE")] * 5)

    with pytest.raises(RuntimeError, match="503"):
        _decide(client)

    assert _calls(client) == 3


@pytest.mark.integration
def test_gemini_returns_a_usable_decision():
    """SH001 is *first* in the queue, so only SH001 itself can be acted on.

    This test used to demand an action on SH002, the cut shot — which reads as
    the obvious sacrifice until you notice SH002 renders after SH001 and giving
    it up returns no time at all. The model duly proposed preempting it "to free
    up farm resources", and the assertion happily passed. Both were wrong.
    """
    from callsheet.config import Config
    from callsheet.decide import decide

    decision = decide(Config.from_env(os.environ), SHOTS, REVIEW, FORECASTS)

    assert decision.summary
    assert decision.actions, "a forecast miss should produce at least one action"
    assert all(a.action in {"preempt", "downgrade", "escalate"} for a in decision.actions)
    assert any(a.shot_id == "SH001" for a in decision.actions)
    assert not any(a.shot_id == "SH002" for a in decision.actions), \
        "SH002 sits behind SH001 in the queue; sacrificing it recovers nothing"
