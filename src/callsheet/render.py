"""Blender subprocess wrapper. A failed render is a value, not an exception."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderResult:
    shot: str
    frame: int
    duration_ms: float
    succeeded: bool
    exit_code: int
    stderr: str


def render_frame(
    blender_path: str,
    scene: str,
    shot: str,
    frame: int,
    out_dir: str,
    timeout_s: int = 600,
) -> RenderResult:
    """Render one frame in Blender's background mode.

    Sample count is baked into the .blend by scenes/make_scenes.py, so it is not
    a parameter here. Phase 2 introduces proxy/final quality tiers by overriding
    bpy.context.scene.cycles.samples through Blender's --python-expr flag.
    """
    command = [
        blender_path,
        "-b",
        scene,
        "-o",
        f"{out_dir}/{shot}_",
        "-F",
        "PNG",
        "-f",
        str(frame),
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return RenderResult(shot, frame, elapsed_ms, False, -1, f"Render timed out after {timeout_s}s")

    elapsed_ms = (time.perf_counter() - started) * 1000
    return RenderResult(
        shot=shot,
        frame=frame,
        duration_ms=elapsed_ms,
        succeeded=completed.returncode == 0,
        exit_code=completed.returncode,
        stderr=completed.stderr or "",
    )
