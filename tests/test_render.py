import os
import tempfile

import pytest

import subprocess
from unittest.mock import patch

from callsheet.render import render_frame


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def test_successful_render_reports_the_shot_and_frame():
    with patch("subprocess.run", return_value=_completed(0)):
        result = render_frame("blender.exe", "scenes/a.blend", "SH001", 1, "out")
    assert result.succeeded is True
    assert result.exit_code == 0
    assert isinstance(result.duration_ms, float)
    assert result.shot == "SH001"
    assert result.frame == 1


def test_failed_render_is_returned_not_raised():
    with patch("subprocess.run", return_value=_completed(1, "Error: out of memory")):
        result = render_frame("blender.exe", "scenes/a.blend", "SH002", 7, "out")
    assert result.succeeded is False
    assert result.exit_code == 1
    assert "out of memory" in result.stderr


def test_command_passes_background_and_frame_flags():
    with patch("subprocess.run", return_value=_completed(0)) as run:
        render_frame("blender.exe", "scenes/a.blend", "SH003", 12, "out")
    command = run.call_args[0][0]
    assert command[0] == "blender.exe"
    assert "-b" in command
    assert "scenes/a.blend" in command
    assert command[command.index("-f") + 1] == "12", "frame number must follow -f"


def test_no_python_expr_when_samples_are_not_overridden():
    with patch("subprocess.run", return_value=_completed(0)) as run:
        render_frame("blender.exe", "a.blend", "SH001", 1, "out")
    assert "--python-expr" not in run.call_args[0][0]


def test_samples_override_goes_after_the_blend_and_before_the_frame():
    """Blender applies --python-expr in argument order: too early and there is no
    scene, too late and the render has already been queued."""
    with patch("subprocess.run", return_value=_completed(0)) as run:
        render_frame("blender.exe", "a.blend", "SH003", 12, "out", samples_override=64)
    command = run.call_args[0][0]
    expr = command.index("--python-expr")
    assert command.index("a.blend") < expr < command.index("-f")
    assert "cycles.samples = 64" in command[expr + 1]


def test_timeout_is_reported_as_failure():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="blender", timeout=1)):
        result = render_frame("blender.exe", "scenes/a.blend", "SH004", 1, "out", timeout_s=1)
    assert result.succeeded is False
    assert result.exit_code == -1
    assert "timed out" in result.stderr.lower()


def test_output_path_is_absolute():
    r"""Blender resolves a bare relative -o against the drive root on Windows.

    A relative path here silently wrote every frame to C:\out\ for the whole of
    phases 1-3, and nothing caught it because success was measured by exit code
    alone.
    """
    with patch("subprocess.run", return_value=_completed(0)) as run:
        render_frame("blender.exe", "a.blend", "SH001", 1, "out")
    command = run.call_args[0][0]
    target = command[command.index("-o") + 1]
    assert os.path.isabs(target), f"-o must be absolute, got {target!r}"
    assert target.endswith("SH001_")


@pytest.mark.integration
def test_a_successful_render_actually_writes_a_frame():
    r"""The assertion whose absence hid the C:\out\ bug for three phases."""
    blender = os.environ.get("BLENDER_PATH")
    if not blender:
        pytest.skip("BLENDER_PATH is not set")

    with tempfile.TemporaryDirectory() as out_dir:
        result = render_frame(blender, "scenes/SH001.blend", "SH001", 1, out_dir,
                              samples_override=4)
        assert result.succeeded, result.stderr
        written = os.listdir(out_dir)
        assert written, "render reported success but produced no file"
        assert any(name.endswith(".png") for name in written), written
