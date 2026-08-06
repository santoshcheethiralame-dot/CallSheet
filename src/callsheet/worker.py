"""Renders a manifest of shots, recording telemetry for each frame."""

from __future__ import annotations

import json

from callsheet.config import Config
from callsheet.render import RenderResult, render_frame
from callsheet.telemetry import Telemetry

SEQUENCE = "SEQ01"


def run_manifest(
    config: Config,
    telemetry: Telemetry,
    manifest_path: str,
    quality: str = "proxy",
    out_dir: str = "out",
) -> list[RenderResult]:
    """Render every frame of every shot in the manifest, recording each one.

    The caller owns the Telemetry object's lifetime: nothing is flushed to
    Grafana Cloud until it is shut down, so hold it in a `with` block.
    """
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    results: list[RenderResult] = []
    for entry in manifest:
        for frame in entry["frames"]:
            result = render_frame(
                blender_path=config.blender_path,
                scene=entry["scene"],
                shot=entry["shot"],
                frame=frame,
                out_dir=out_dir,
            )
            telemetry.record_render(result, sequence=SEQUENCE, quality=quality)
            results.append(result)
    return results
