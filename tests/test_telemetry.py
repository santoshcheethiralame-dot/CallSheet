from unittest.mock import patch

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from callsheet.render import RenderResult
from callsheet.telemetry import Telemetry


def _result(shot="SH001", succeeded=True, duration_ms=1234.0) -> RenderResult:
    return RenderResult(
        shot=shot, frame=1, duration_ms=duration_ms,
        succeeded=succeeded, exit_code=0 if succeeded else 1,
        stderr="" if succeeded else "Error: out of memory",
    )


def _duration_points(reader):
    return [
        point
        for rm in reader.get_metrics_data().resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "render.frame.duration"
        for point in metric.data.data_points
    ]


def test_record_render_emits_duration_histogram():
    reader = InMemoryMetricReader()
    telemetry = Telemetry.for_testing(reader)

    telemetry.record_render(_result(), sequence="SEQ01", quality="proxy")

    metrics = reader.get_metrics_data()
    names = {
        metric.name
        for rm in metrics.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
    }
    assert "render.frame.duration" in names


def test_duration_carries_shot_and_quality_attributes():
    reader = InMemoryMetricReader()
    telemetry = Telemetry.for_testing(reader)

    telemetry.record_render(_result(shot="SH114"), sequence="SEQ02", quality="final")

    points = [
        point
        for rm in reader.get_metrics_data().resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "render.frame.duration"
        for point in metric.data.data_points
    ]
    assert len(points) == 1
    attributes = dict(points[0].attributes)
    assert attributes["shot"] == "SH114"
    assert attributes["sequence"] == "SEQ02"
    assert attributes["quality"] == "final"
    assert attributes["outcome"] == "success"


def test_failed_render_is_labelled_failure():
    reader = InMemoryMetricReader()
    telemetry = Telemetry.for_testing(reader)

    telemetry.record_render(_result(succeeded=False), sequence="SEQ01", quality="proxy")

    points = [
        point
        for rm in reader.get_metrics_data().resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "render.frame.duration"
        for point in metric.data.data_points
    ]
    assert dict(points[0].attributes)["outcome"] == "failure"


def test_with_block_yields_the_telemetry_and_records():
    reader = InMemoryMetricReader()

    with Telemetry.for_testing(reader) as telemetry:
        assert isinstance(telemetry, Telemetry)
        telemetry.record_render(_result(), sequence="SEQ01", quality="proxy")
        points = _duration_points(reader)

    assert sum(point.count for point in points) == 1


def test_leaving_the_block_flushes():
    telemetry = Telemetry.for_testing(InMemoryMetricReader())

    with patch.object(telemetry, "shutdown") as shutdown:
        with telemetry:
            pass

    shutdown.assert_called_once_with()


def test_shutdown_runs_even_when_the_block_raises():
    """A crash is exactly when the telemetry matters most, so it must still flush."""
    telemetry = Telemetry.for_testing(InMemoryMetricReader())

    with patch.object(telemetry, "shutdown") as shutdown:
        with pytest.raises(RuntimeError, match="render crashed"):
            with telemetry:
                raise RuntimeError("render crashed")

    shutdown.assert_called_once_with()
