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


def test_timeout_is_reported_as_failure():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="blender", timeout=1)):
        result = render_frame("blender.exe", "scenes/a.blend", "SH004", 1, "out", timeout_s=1)
    assert result.succeeded is False
    assert result.exit_code == -1
    assert "timed out" in result.stderr.lower()
