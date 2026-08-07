import json
import os

import pytest

from callsheet.farm_state import parse_farm_state

MEANS = json.dumps({"data": [
    {"metric": {"shot": "SH001", "quality": "final"}, "value": [1786047271.417, "5146.94"]},
    {"metric": {"shot": "SH001", "quality": "proxy"}, "value": [1786047271.417, "1930.11"]},
    {"metric": {"shot": "SH003", "quality": "final"}, "value": [1786047271.417, "26646.55"]},
]})


def test_parses_mean_frame_duration_per_shot():
    state = parse_farm_state(MEANS)
    assert state.mean_frame_ms[("SH001", "final")] == pytest.approx(5146.94)
    assert state.mean_frame_ms[("SH003", "final")] == pytest.approx(26646.55)


def test_rate_is_keyed_by_shot_and_quality():
    state = parse_farm_state(MEANS)
    assert state.mean_frame_ms[("SH001", "final")] == pytest.approx(5146.94)
    assert state.mean_frame_ms[("SH001", "proxy")] == pytest.approx(1930.11)


def test_series_without_a_quality_label_is_ignored():
    """Pre-Phase-3 series carry no quality. They must not silently become 'final'."""
    legacy = json.dumps({"data": [{"metric": {"shot": "SH001"}, "value": [0, "5000"]}]})
    assert parse_farm_state(legacy).mean_frame_ms == {}


def test_frames_done_is_never_read_from_telemetry():
    """Grafana knows the rate. Only the queue knows what is left to do."""
    state = parse_farm_state(MEANS)
    assert state.frames_done == {}


def test_empty_response_yields_empty_state_not_an_error():
    """A cold farm has no series yet. That is normal, not a failure."""
    state = parse_farm_state(json.dumps({"data": []}))
    assert state.mean_frame_ms == {}


def test_series_without_a_shot_label_is_ignored():
    noisy = json.dumps({"data": [{"metric": {}, "value": [0, "123"]}]})
    assert parse_farm_state(noisy).mean_frame_ms == {}


def test_nan_mean_is_dropped_rather_than_poisoning_the_forecast():
    """rate() over a window with one sample yields NaN. It must not become 0.0."""
    nan_means = json.dumps({"data": [
        {"metric": {"shot": "SH001", "quality": "final"}, "value": [0, "NaN"]},
    ]})
    assert ("SH001", "final") not in parse_farm_state(nan_means).mean_frame_ms


def test_prometheus_http_api_shape_is_also_accepted():
    """mcp-grafana may hand back {"data": {"result": [...]}} rather than {"data": [...]}."""
    wrapped = json.dumps({"data": {"result": [
        {"metric": {"shot": "SH001", "quality": "final"}, "value": [0, "1234.5"]},
    ]}})
    assert parse_farm_state(wrapped).mean_frame_ms[("SH001", "final")] == pytest.approx(1234.5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reads_real_farm_state_from_grafana():
    from callsheet.config import Config
    from callsheet.farm_state import read_farm_state

    state = await read_farm_state(Config.from_env(os.environ))
    assert state.mean_frame_ms, "expected the Phase 1 render to still be visible"
    for (shot, quality), mean in state.mean_frame_ms.items():
        assert mean > 0, f"{shot}/{quality} has a non-positive mean"
    assert {quality for _, quality in state.mean_frame_ms} == {"final", "proxy"}, (
        "both tiers must be measured, or a downgrade's benefit is still a guess"
    )
