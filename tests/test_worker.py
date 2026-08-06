import json
from unittest.mock import patch

from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from callsheet.config import Config
from callsheet.render import RenderResult
from callsheet.telemetry import Telemetry
from callsheet.worker import run_manifest

CONFIG = Config(
    grafana_url="https://x.grafana.net",
    grafana_token="glsa_abc",
    otlp_endpoint="https://otlp.example/otlp",
    otlp_auth="aGVsbG8=",
    blender_path="blender.exe",
)


def _write_manifest(tmp_path):
    manifest = [
        {"shot": "SH001", "scene": "scenes/SH001.blend", "samples": 16, "frames": [1, 2]},
        {"shot": "SH002", "scene": "scenes/SH002.blend", "samples": 64, "frames": [1]},
    ]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return str(path)


def test_runs_every_frame_of_every_shot(tmp_path):
    reader = InMemoryMetricReader()
    telemetry = Telemetry.for_testing(reader)
    fake = RenderResult("SH001", 1, 100.0, True, 0, "")

    with patch("callsheet.worker.render_frame", return_value=fake) as render:
        results = run_manifest(CONFIG, telemetry, _write_manifest(tmp_path))

    assert render.call_count == 3
    assert len(results) == 3


def test_records_one_metric_point_per_frame(tmp_path):
    reader = InMemoryMetricReader()
    telemetry = Telemetry.for_testing(reader)
    fake = RenderResult("SH001", 1, 100.0, True, 0, "")

    with patch("callsheet.worker.render_frame", return_value=fake):
        run_manifest(CONFIG, telemetry, _write_manifest(tmp_path))

    points = [
        point
        for rm in reader.get_metrics_data().resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "render.frame.duration"
        for point in metric.data.data_points
    ]
    assert sum(point.count for point in points) == 3
