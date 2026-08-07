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


@pytest.mark.integration
def test_proxy_renders_measurably_faster_than_final():
    """A downgrade must buy real time, and we must know how much."""
    from callsheet.render import render_frame

    blender = os.environ.get("BLENDER_PATH")
    if not blender:
        pytest.skip("BLENDER_PATH is not set")
    if not os.path.exists(MANIFEST):
        pytest.skip("scenes not generated yet")

    with open(MANIFEST, encoding="utf-8") as handle:
        entry = json.load(handle)[-1]          # SH003, the expensive one

    # Best of two per tier. Cycles renders on the CPU, so a frame that happens to
    # run against background load can take twice as long as the same frame on an
    # idle machine; observed spread on this laptop is roughly 2x. The minimum is
    # the least contaminated estimate of what the render actually costs, and
    # comparing single runs makes this test fail on scheduler luck rather than on
    # the thing it is meant to measure.
    def best(**kwargs) -> float:
        runs = [render_frame(blender, entry["scene"], entry["shot"], 1, "out", **kwargs)
                for _ in range(2)]
        assert all(run.succeeded for run in runs), [run.stderr for run in runs]
        return min(run.duration_ms for run in runs)

    final = best()
    proxy = best(samples_override=entry["samples"] // 4)

    assert proxy < final, f"proxy {proxy:.0f}ms was not faster than final {final:.0f}ms"
    print(f"\n{entry['shot']} final={final:.0f}ms proxy={proxy:.0f}ms "
          f"speedup={final / proxy:.2f}x")
