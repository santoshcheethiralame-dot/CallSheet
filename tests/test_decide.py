import os

import pytest

from callsheet.decide import build_prompt, parse_decision
from callsheet.domain import Review, Shot
from callsheet.forecast import Forecast

NOW = 1_000_000

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


@pytest.mark.integration
def test_gemini_returns_a_usable_decision():
    from callsheet.config import Config
    from callsheet.decide import decide

    decision = decide(Config.from_env(os.environ), SHOTS, REVIEW, FORECASTS)

    assert decision.summary
    assert decision.actions, "a forecast miss should produce at least one action"
    assert all(a.action in {"preempt", "downgrade", "escalate"} for a in decision.actions)
    # The sensible call is to sacrifice the cut shot, not the hero shot.
    assert any(a.shot_id == "SH002" for a in decision.actions)
