import json
import os

import pytest

MANIFEST = "scenes/manifest.json"


def test_manifest_is_well_formed():
    if not os.path.exists(MANIFEST):
        pytest.skip("scenes not generated yet")
    with open(MANIFEST, encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert [entry["shot"] for entry in manifest] == ["SH001", "SH002", "SH003"]
    samples = [entry["samples"] for entry in manifest]
    assert samples == sorted(samples), "render cost must increase across shots"


@pytest.mark.integration
def test_render_cost_actually_increases():
    """The whole spike rests on shots having genuinely different cost."""
    from callsheet.render import render_frame

    # Only BLENDER_PATH is needed here, so only BLENDER_PATH is demanded. Building
    # a full Config would drag in the Grafana credentials this test never touches.
    blender_path = os.environ.get("BLENDER_PATH")
    if not blender_path:
        pytest.skip("BLENDER_PATH is not set")
    if not os.path.exists(MANIFEST):
        pytest.skip("scenes not generated yet")

    with open(MANIFEST, encoding="utf-8") as handle:
        manifest = json.load(handle)

    durations = [
        render_frame(blender_path, entry["scene"], entry["shot"], 1, "out").duration_ms
        for entry in manifest
    ]
    assert durations[0] < durations[-1], f"expected increasing cost, got {durations}"
