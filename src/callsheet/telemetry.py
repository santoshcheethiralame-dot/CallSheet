"""OpenTelemetry wiring. Exports to Grafana Cloud in production, in-memory in tests."""

from __future__ import annotations

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from callsheet.config import Config
from callsheet.render import RenderResult

SERVICE_NAME = "callsheet-worker"


class Telemetry:
    """Owns the meter provider and the instruments recorded against it.

    Use it as a context manager so the flush cannot be forgotten:

        with Telemetry.for_grafana(config) as telemetry:
            run_manifest(config, telemetry, "scenes/manifest.json")
    """

    def __init__(self, provider: MeterProvider) -> None:
        self._provider = provider
        meter = provider.get_meter("callsheet")
        self._duration = meter.create_histogram(
            name="render.frame.duration",
            unit="ms",
            description="Wall-clock time to render one frame",
        )

    @classmethod
    def for_grafana(cls, config: Config) -> "Telemetry":
        exporter = OTLPMetricExporter(
            endpoint=f"{config.otlp_endpoint.rstrip('/')}/v1/metrics",
            headers={"Authorization": f"Basic {config.otlp_auth}"},
        )
        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=5000)
        resource = Resource.create({"service.name": SERVICE_NAME})
        return cls(MeterProvider(resource=resource, metric_readers=[reader]))

    @classmethod
    def for_testing(cls, reader) -> "Telemetry":
        resource = Resource.create({"service.name": SERVICE_NAME})
        return cls(MeterProvider(resource=resource, metric_readers=[reader]))

    def __enter__(self) -> "Telemetry":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Flush on the way out, including when the body raised.

        Nothing reaches Grafana Cloud until shutdown() runs, and a crashed
        render is precisely when its telemetry is worth keeping.
        """
        self.shutdown()

    def record_render(self, result: RenderResult, sequence: str, quality: str) -> None:
        self._duration.record(
            result.duration_ms,
            attributes={
                "shot": result.shot,
                "sequence": sequence,
                "quality": quality,
                "outcome": "success" if result.succeeded else "failure",
            },
        )

    def shutdown(self) -> None:
        """Flush pending exports. Must be called before process exit."""
        self._provider.shutdown()
