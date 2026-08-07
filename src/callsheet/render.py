"""Blender subprocess wrapper. A failed render is a value, not an exception."""

from __future__ import annotations

import os
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
    samples_override: int | None = None,
) -> RenderResult:
    """Render one frame in Blender's background mode.

    Sample count is normally baked into the .blend by scenes/make_scenes.py.
    `samples_override` lowers it for proxy-quality renders; Blender has no CLI
    flag for this, so it goes through --python-expr. Argument order is
    load-bearing: the expression must follow the .blend, or it runs before there
    is a scene to change, and it must precede -f, or the render is already
    queued at the baked sample count by the time it runs.
    """
    command = [blender_path, "-b", scene]
    if samples_override is not None:
        command += [
            "--python-expr",
            f"import bpy; bpy.context.scene.cycles.samples = {samples_override}",
        ]
    # Absolute, always. Blender resolves a bare relative output path against the
    # drive root on Windows, not the working directory: "out/" silently became
    # "C:\out\". Every render still reported success, because success was only
    # ever measured by exit code.
    command += [
        "-o",
        os.path.join(os.path.abspath(out_dir), f"{shot}_"),
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
