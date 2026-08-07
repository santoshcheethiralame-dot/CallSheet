"""Renders a manifest of shots, recording telemetry for each frame."""

from __future__ import annotations

import json

from callsheet.config import Config
from callsheet.render import RenderResult, render_frame
from callsheet.telemetry import Telemetry

SEQUENCE = "SEQ01"

PROXY_SAMPLE_DIVISOR = 4
"""A proxy frame renders at a quarter of the shot's baked sample count."""


def run_manifest(
    config: Config,
    telemetry: Telemetry,
    manifest_path: str,
    quality: str = "final",
    out_dir: str = "out",
) -> list[RenderResult]:
    """Render every frame of every shot in the manifest, recording each one.

    `quality` both labels the telemetry and decides what is actually rendered:
    `"proxy"` drops the sample count to a quarter of the manifest's. Deriving
    one from the other is deliberate — a label chosen independently of the
    render could report a proxy rate that was measured at final quality, and
    the forecaster would then plan against a speedup that never happened.

    The caller owns the Telemetry object's lifetime: nothing is flushed to
    Grafana Cloud until it is shut down, so hold it in a `with` block.
    """
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    results: list[RenderResult] = []
    for entry in manifest:
        samples_override = (
            entry["samples"] // PROXY_SAMPLE_DIVISOR if quality == "proxy" else None
        )
        for frame in entry["frames"]:
            result = render_frame(
                blender_path=config.blender_path,
                scene=entry["scene"],
                shot=entry["shot"],
                frame=frame,
                out_dir=out_dir,
                samples_override=samples_override,
            )
            telemetry.record_render(result, sequence=SEQUENCE, quality=quality)
            results.append(result)
    return results
