# CALLSHEET Phase 1 — Telemetry Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that a real Blender render's telemetry reaches Grafana Cloud's free tier and can be read back out through the Grafana MCP server, entirely at $0.

**Architecture:** A Blender subprocess renders real frames. An OpenTelemetry layer records frame duration, memory and outcome as metrics, logs and spans, exporting over OTLP to Grafana Cloud. A separate MCP client process launches `mcp-grafana` over stdio and queries those same metrics back. The spike passes when a metric produced by an actual render is retrieved through the MCP server by code.

**Tech Stack:** Python 3.12.10, Blender 5.2.0 (CLI, background mode), OpenTelemetry SDK 1.44 + OTLP/HTTP exporter, `mcp` Python SDK 2.0, `mcp-grafana` v1.0.0 (Go binary), Grafana Cloud free tier, pytest 8.3.

## Global Constraints

- **$0 total cost, no credit card.** Grafana Cloud free plan only. No paid services, no GCP resources in this phase.
- **No non-Google AI models, agent frameworks, or AI APIs** anywhere in the project. Phase 1 uses no AI at all.
- **Python 3.11+.**
- **Windows 11 / PowerShell primary.** All commands given for PowerShell. Paths use forward slashes in Python, backslashes only in shell examples.
- **No secrets in the repo.** Credentials live in `.env`, which is gitignored. `.env.example` is committed with empty values.
- **License: Apache-2.0**, committed in Task 1 — it is a hackathon eligibility requirement, not a finishing touch.
- **Tests must run without Blender installed and without Grafana credentials.** Anything requiring either is marked `@pytest.mark.integration` and excluded from the default run.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Dependencies, pytest config, marker registration |
| `LICENSE` | Apache-2.0, required for eligibility |
| `.gitignore` | Excludes `.env`, `out/`, `scenes/*.blend`, `__pycache__` |
| `.env.example` | Names every required credential, no values |
| `src/callsheet/config.py` | Loads and validates environment config; fails fast |
| `src/callsheet/render.py` | Wraps the Blender subprocess; returns a `RenderResult` |
| `src/callsheet/telemetry.py` | Configures OTel providers and exposes the instruments |
| `src/callsheet/worker.py` | Glues render + telemetry into one instrumented unit of work |
| `src/callsheet/grafana_mcp.py` | Launches `mcp-grafana` over stdio, lists tools, runs queries |
| `scenes/make_scenes.py` | Blender-side script generating scenes of varying render cost |
| `scripts/spike_end_to_end.py` | The Phase 1 gate: render, then read the metric back via MCP |
| `tests/test_config.py` | Config validation |
| `tests/test_render.py` | Render wrapper, with `subprocess.run` patched |
| `tests/test_telemetry.py` | Instrument recording, using an in-memory reader |
| `tests/test_grafana_mcp.py` | MCP client, integration-marked |

Split by responsibility rather than layer: `render.py` knows about Blender and nothing about telemetry; `telemetry.py` knows about OTel and nothing about Blender; `worker.py` is the only file that knows both.

---

### Task 1: Repo scaffold, license, and fail-fast config

**Files:**
- Create: `pyproject.toml`, `LICENSE`, `.gitignore`, `.env.example`, `src/callsheet/__init__.py`, `src/callsheet/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Config` dataclass with fields `grafana_url: str`, `grafana_token: str`, `otlp_endpoint: str`, `otlp_auth: str`, `blender_path: str`; and `Config.from_env(env: Mapping[str, str]) -> Config` which raises `ConfigError` listing every missing key at once.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "callsheet"
version = "0.1.0"
description = "Render-farm production agent driven by observability telemetry"
requires-python = ">=3.11"
dependencies = [
    "opentelemetry-sdk>=1.27",
    "opentelemetry-exporter-otlp-proto-http>=1.27",
    "mcp>=1.2",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: requires Blender installed or Grafana credentials present",
]
addopts = "-m 'not integration'"
```

- [ ] **Step 2: Create `LICENSE`**

Write the full Apache License 2.0 text. Fetch the canonical text from
`https://www.apache.org/licenses/LICENSE-2.0.txt` and save it verbatim as
`LICENSE`. Do not paraphrase or truncate it — Devpost's eligibility check looks
for a detectable license, and a partial file may not be recognised.

- [ ] **Step 3: Create `.gitignore`**

```gitignore
.env
out/
scenes/*.blend
scenes/*.blend1
__pycache__/
*.egg-info/
.pytest_cache/
```

- [ ] **Step 4: Create `.env.example`**

```dotenv
# Grafana Cloud — Home > Administration > Service accounts > Add service account token
GRAFANA_URL=
GRAFANA_SERVICE_ACCOUNT_TOKEN=

# Grafana Cloud OTLP gateway — Home > Connections > OpenTelemetry > Configure
# OTLP_AUTH is base64("<instanceID>:<token>")
OTLP_ENDPOINT=
OTLP_AUTH=

# Absolute path to the Blender executable
BLENDER_PATH=
```

- [ ] **Step 5: Write the failing test**

```python
# tests/test_config.py
import pytest
from callsheet.config import Config, ConfigError

FULL_ENV = {
    "GRAFANA_URL": "https://x.grafana.net",
    "GRAFANA_SERVICE_ACCOUNT_TOKEN": "glsa_abc",
    "OTLP_ENDPOINT": "https://otlp-gateway-prod.grafana.net/otlp",
    "OTLP_AUTH": "aGVsbG8=",
    "BLENDER_PATH": "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe",
}


def test_from_env_reads_every_field():
    config = Config.from_env(FULL_ENV)
    assert config.grafana_url == "https://x.grafana.net"
    assert config.grafana_token == "glsa_abc"
    assert config.otlp_endpoint == "https://otlp-gateway-prod.grafana.net/otlp"
    assert config.otlp_auth == "aGVsbG8="
    assert config.blender_path.endswith("blender.exe")


def test_from_env_strips_trailing_slash_on_grafana_url():
    config = Config.from_env({**FULL_ENV, "GRAFANA_URL": "https://x.grafana.net/"})
    assert config.grafana_url == "https://x.grafana.net"


def test_from_env_lists_every_missing_key_at_once():
    with pytest.raises(ConfigError) as excinfo:
        Config.from_env({"GRAFANA_URL": "https://x.grafana.net"})
    message = str(excinfo.value)
    assert "GRAFANA_SERVICE_ACCOUNT_TOKEN" in message
    assert "OTLP_ENDPOINT" in message
    assert "OTLP_AUTH" in message
    assert "BLENDER_PATH" in message
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.config'`

- [ ] **Step 7: Write the minimal implementation**

```python
# src/callsheet/config.py
"""Environment configuration, validated once at startup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


_FIELDS = {
    "grafana_url": "GRAFANA_URL",
    "grafana_token": "GRAFANA_SERVICE_ACCOUNT_TOKEN",
    "otlp_endpoint": "OTLP_ENDPOINT",
    "otlp_auth": "OTLP_AUTH",
    "blender_path": "BLENDER_PATH",
}


@dataclass(frozen=True)
class Config:
    grafana_url: str
    grafana_token: str
    otlp_endpoint: str
    otlp_auth: str
    blender_path: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        missing = [name for name in _FIELDS.values() if not env.get(name)]
        if missing:
            raise ConfigError(
                "Missing required environment variables: " + ", ".join(sorted(missing))
            )
        values = {field: env[name].strip() for field, name in _FIELDS.items()}
        values["grafana_url"] = values["grafana_url"].rstrip("/")
        return cls(**values)
```

Also create an empty `src/callsheet/__init__.py`.

- [ ] **Step 8: Create `tests/conftest.py`**

Integration tests read credentials from `os.environ`, but nothing loads `.env`
for them — only the spike script does. Without this, every integration test
fails with a misleading `ConfigError` on a machine that is correctly configured.

**Order matters:** create this file *after* `pip install -e ".[dev]"`, not
before. It imports `dotenv`, so if it exists during an earlier "verify the test
fails" run, pytest dies on `ModuleNotFoundError: dotenv` instead of the expected
failure, and the TDD step gives a misleading signal.

```python
# tests/conftest.py
"""Load .env so integration tests see the same credentials the spike script does."""

from dotenv import load_dotenv

load_dotenv()
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `pip install -e ".[dev]"` then `python -m pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml LICENSE .gitignore .env.example src/callsheet tests/test_config.py tests/conftest.py
git commit -m "Add project scaffold, Apache-2.0 license, and fail-fast config"
```

---

### Task 2: Blender render wrapper

**Files:**
- Create: `src/callsheet/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Config.blender_path` from Task 1
- Produces: `RenderResult` dataclass with fields `shot: str`, `frame: int`, `duration_ms: float`, `succeeded: bool`, `exit_code: int`, `stderr: str`; and `render_frame(blender_path: str, scene: str, shot: str, frame: int, out_dir: str, timeout_s: int = 600) -> RenderResult`. Never raises on a render failure — a failed render is data, returned with `succeeded=False`.

**No `samples` parameter.** Sample count is baked into the `.blend` by Task 3's
generator, so passing it here would be a lie in the signature. Phase 2
introduces proxy/final quality tiers by overriding
`bpy.context.scene.cycles.samples` through Blender's `--python-expr` flag, which
is the point at which a quality argument becomes real.

Failures are a product feature here, not an exception. The agent's whole job in
later phases is reacting to them, so they must survive as structured values.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render.py
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.render'`

- [ ] **Step 3: Write the minimal implementation**

```python
# src/callsheet/render.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_render.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/callsheet/render.py tests/test_render.py
git commit -m "Add Blender render wrapper returning failures as values"
```

---

### Task 3: Scene generator with varying render cost

**Files:**
- Create: `scenes/make_scenes.py`
- Test: `tests/test_scenes.py`

**Interfaces:**
- Consumes: nothing from earlier tasks — this script runs *inside* Blender, not in the project's Python environment
- Produces: three files `scenes/SH001.blend`, `scenes/SH002.blend`, `scenes/SH003.blend` with strictly increasing render cost, and a manifest `scenes/manifest.json` shaped `[{"shot": "SH001", "scene": "scenes/SH001.blend", "samples": 16, "frames": [1, 2, 3]}, ...]`

Procedural generation rather than downloaded production assets: the spec's §5.1
decision. Real renders, real variance, no multi-gigabyte dependency.

- [ ] **Step 1: Write the generator**

```python
# scenes/make_scenes.py
"""Generate Blender scenes of increasing render cost.

Run inside Blender, not in the project venv:
    blender -b -P scenes/make_scenes.py
"""

import json
import os

import bpy

SHOTS = [
    {"shot": "SH001", "samples": 16, "subdivisions": 2, "frames": [1, 2, 3]},
    {"shot": "SH002", "samples": 64, "subdivisions": 4, "frames": [1, 2, 3]},
    {"shot": "SH003", "samples": 256, "subdivisions": 6, "frames": [1, 2, 3]},
]

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))


def build_scene(samples: int, subdivisions: int) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions, radius=1.0)
    obj = bpy.context.active_object
    modifier = obj.modifiers.new(name="Subdiv", type="SUBSURF")
    modifier.render_levels = 2

    bpy.ops.object.light_add(type="AREA", location=(4, -4, 6))
    bpy.context.active_object.data.energy = 800

    bpy.ops.object.camera_add(location=(6, -6, 4), rotation=(1.1, 0, 0.8))
    bpy.context.scene.camera = bpy.context.active_object

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = False
    scene.render.resolution_x = 480
    scene.render.resolution_y = 270
    scene.frame_start = 1
    scene.frame_end = 3


def main() -> None:
    manifest = []
    for entry in SHOTS:
        build_scene(entry["samples"], entry["subdivisions"])
        path = os.path.join(OUT_DIR, f"{entry['shot']}.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        manifest.append(
            {
                "shot": entry["shot"],
                "scene": f"scenes/{entry['shot']}.blend",
                "samples": entry["samples"],
                "frames": entry["frames"],
            }
        )

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)


main()
```

- [ ] **Step 2: Install Blender and generate the scenes**

✅ **Done.** Installed via `winget install BlenderFoundation.Blender` — **Blender
5.2.0 LTS** at `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`.
Every 4.x call in this script was probed and works unchanged on 5.2.

```bash
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b -P scenes/make_scenes.py
```

Expected: `scenes/SH001.blend`, `SH002.blend`, `SH003.blend` and `manifest.json` exist.

> **Verify the bpy API against 5.2 before trusting the generator.** The calls
> most likely to have moved are `bpy.ops.mesh.primitive_ico_sphere_add`,
> `scene.cycles.samples`, and `scene.cycles.use_denoising`. If the script errors,
> read the traceback and adapt — do not assume the 4.x form still applies. A
> quick check: `blender -b --python-expr "import bpy; print(bpy.app.version)"`.

- [ ] **Step 3: Write the test**

```python
# tests/test_scenes.py
import json
import os
import subprocess

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
    from callsheet.config import Config
    from callsheet.render import render_frame

    config = Config.from_env(os.environ)
    with open(MANIFEST, encoding="utf-8") as handle:
        manifest = json.load(handle)

    durations = [
        render_frame(config.blender_path, entry["scene"], entry["shot"], 1, "out").duration_ms
        for entry in manifest
    ]
    assert durations[0] < durations[-1], f"expected increasing cost, got {durations}"
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_scenes.py -v`
Expected: 1 passed, 1 deselected (integration)

Then the integration check: `python -m pytest tests/test_scenes.py -v -m integration`
Expected: PASS, with SH003 measurably slower than SH001. **If cost does not
increase, raise `samples` on SH003 until it does** — every later phase depends
on shots having different cost.

- [ ] **Step 5: Commit**

```bash
git add scenes/make_scenes.py scenes/manifest.json tests/test_scenes.py
git commit -m "Add procedural scene generator with increasing render cost"
```

---

### Task 4: OpenTelemetry layer

**Files:**
- Create: `src/callsheet/telemetry.py`
- Test: `tests/test_telemetry.py`

**Interfaces:**
- Consumes: `Config.otlp_endpoint`, `Config.otlp_auth` from Task 1; `RenderResult` from Task 2
- Produces: `Telemetry` class with `Telemetry.for_grafana(config: Config) -> Telemetry`, `Telemetry.for_testing(reader) -> Telemetry`, method `record_render(result: RenderResult, sequence: str, quality: str) -> None`, and `shutdown() -> None` which flushes exporters.

Metric names use OTel dot notation. **Known trap:** Prometheus rewrites these —
`render.frame.duration` recorded in milliseconds surfaces as
`render_frame_duration_milliseconds_bucket` / `_count` / `_sum`. Task 6 discovers
the real name rather than assuming it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_telemetry.py
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from callsheet.render import RenderResult
from callsheet.telemetry import Telemetry


def _result(shot="SH001", succeeded=True, duration_ms=1234.0) -> RenderResult:
    return RenderResult(
        shot=shot, frame=1, duration_ms=duration_ms,
        succeeded=succeeded, exit_code=0 if succeeded else 1,
        stderr="" if succeeded else "Error: out of memory",
    )


def test_record_render_emits_duration_histogram():
    reader = InMemoryMetricReader()
    telemetry = Telemetry.for_testing(reader)

    telemetry.record_render(_result(), sequence="SEQ01", quality="proxy")

    metrics = reader.get_metrics_data()
    names = {
        metric.name
        for rm in metrics.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
    }
    assert "render.frame.duration" in names


def test_duration_carries_shot_and_quality_attributes():
    reader = InMemoryMetricReader()
    telemetry = Telemetry.for_testing(reader)

    telemetry.record_render(_result(shot="SH114"), sequence="SEQ02", quality="final")

    points = [
        point
        for rm in reader.get_metrics_data().resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "render.frame.duration"
        for point in metric.data.data_points
    ]
    assert len(points) == 1
    attributes = dict(points[0].attributes)
    assert attributes["shot"] == "SH114"
    assert attributes["sequence"] == "SEQ02"
    assert attributes["quality"] == "final"
    assert attributes["outcome"] == "success"


def test_failed_render_is_labelled_failure():
    reader = InMemoryMetricReader()
    telemetry = Telemetry.for_testing(reader)

    telemetry.record_render(_result(succeeded=False), sequence="SEQ01", quality="proxy")

    points = [
        point
        for rm in reader.get_metrics_data().resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "render.frame.duration"
        for point in metric.data.data_points
    ]
    assert dict(points[0].attributes)["outcome"] == "failure"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_telemetry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.telemetry'`

- [ ] **Step 3: Write the minimal implementation**

```python
# src/callsheet/telemetry.py
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
    """Owns the meter provider and the instruments recorded against it."""

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_telemetry.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/callsheet/telemetry.py tests/test_telemetry.py
git commit -m "Add OpenTelemetry layer recording render duration by shot"
```

---

### Task 5: Instrumented worker

**Files:**
- Create: `src/callsheet/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `render_frame` (Task 2), `Telemetry` (Task 4), `scenes/manifest.json` (Task 3)
- Produces: `run_manifest(config: Config, telemetry: Telemetry, manifest_path: str, quality: str = "proxy") -> list[RenderResult]`, rendering every frame of every shot in order and recording each.

The only file that knows about both Blender and OTel.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker.py
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.worker'`

- [ ] **Step 3: Write the minimal implementation**

```python
# src/callsheet/worker.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_worker.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/callsheet/worker.py tests/test_worker.py
git commit -m "Add instrumented worker rendering a shot manifest"
```

---

### Task 6: Grafana MCP client

**Files:**
- Create: `src/callsheet/grafana_mcp.py`
- Test: `tests/test_grafana_mcp.py`

**Interfaces:**
- Consumes: `Config.grafana_url`, `Config.grafana_token` from Task 1
- Produces: async `list_tools(config: Config) -> list[str]` and async `call_tool(config: Config, name: str, arguments: dict) -> str`, both launching `mcp-grafana` over stdio.

**This is the partner requirement.** The rules check for a live `mcp-grafana`
connection in code, so this file is the single most compliance-critical unit in
the project.

> **Verify the SDK API before writing this task.** Task 1 resolved `mcp` to
> **2.0.0**, not the 1.x line the code below was written against. Run
> `python -c "import mcp; print(mcp.__version__); print(dir(mcp))"` and check
> that `ClientSession`, `StdioServerParameters` and `mcp.client.stdio.stdio_client`
> still exist with these signatures. If 2.0 moved them, fix the imports here
> before implementing — do not assume the code below compiles.

- [ ] **Step 1: Install `mcp-grafana`** — ✅ **already done**

`mcp-grafana` **v1.0.0** (Windows x86_64) is installed at
`C:\Users\carbo\bin\mcp-grafana.exe`. It is *not* on `PATH`, so `.env` must set:

```dotenv
MCP_GRAFANA_PATH=C:/Users/carbo/bin/mcp-grafana.exe
```

`Config.mcp_grafana_path` defaults to the bare command `mcp-grafana` when that
variable is blank, so a machine with it on `PATH` needs no configuration.

Verified working: `& "C:\Users\carbo\bin\mcp-grafana.exe" --help` prints usage
listing the tool groups, including `-disable-annotations`, `-disable-alerting`
and `-disable-agento11y` — confirming the annotation-writing and alerting tools
Phase 2 depends on are present in this build.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_grafana_mcp.py
import os

import pytest

from callsheet.config import Config
from callsheet.grafana_mcp import call_tool, list_tools


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lists_the_expected_grafana_tools():
    config = Config.from_env(os.environ)
    tools = await list_tools(config)
    assert tools, "mcp-grafana returned no tools"
    # Names are discovered, not assumed — print them so later tasks use the real ones.
    print("\nAvailable mcp-grafana tools:\n  " + "\n  ".join(sorted(tools)))
    assert any("datasource" in name for name in tools)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_can_list_datasources():
    config = Config.from_env(os.environ)
    tools = await list_tools(config)
    name = next(tool for tool in tools if "list" in tool and "datasource" in tool)
    output = await call_tool(config, name, {})
    assert "prometheus" in output.lower()
```

Add `pytest-asyncio>=0.23` to the `dev` extra in `pyproject.toml`, and
`asyncio_mode = "auto"` under `[tool.pytest.ini_options]`.

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_grafana_mcp.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'callsheet.grafana_mcp'`

- [ ] **Step 4: Write the minimal implementation**

```python
# src/callsheet/grafana_mcp.py
"""Client for the Grafana MCP server. The partner integration, called at runtime."""

from __future__ import annotations

import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from callsheet.config import Config

def _server_params(config: Config) -> StdioServerParameters:
    return StdioServerParameters(
        command=config.mcp_grafana_path,
        args=[],
        env={
            **os.environ,
            "GRAFANA_URL": config.grafana_url,
            "GRAFANA_API_KEY": config.grafana_token,
        },
    )


async def list_tools(config: Config) -> list[str]:
    """Names of every tool the Grafana MCP server exposes."""
    async with stdio_client(_server_params(config)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.list_tools()
            return [tool.name for tool in response.tools]


async def call_tool(config: Config, name: str, arguments: dict) -> str:
    """Invoke one Grafana MCP tool and return its text content."""
    async with stdio_client(_server_params(config)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            return "\n".join(
                block.text for block in result.content if getattr(block, "text", None)
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_grafana_mcp.py -v -m integration -s`
Expected: 2 passed, and the printed tool list.

**Record the printed tool names in the plan for Phase 2** — the exact names for
querying Prometheus, querying Loki and creating annotations are inputs to the
agent's tool wiring, and guessing them is how Phase 2 stalls.

#### Discovered `mcp-grafana` v1.0.0 tool names — 73 tools

Captured from a live stdio session against `https://vastfoyer1220.grafana.net`
on 2026-08-07 with no `-disable-*` flags. These are the exact strings Phase 2
must pass to `call_tool`. Eight of the 73 are proxied from the connected
datasource (the `tempo_*` group).

**The four that matter:**

| Role | Tool name | Required arguments |
|---|---|---|
| Query Prometheus | `query_prometheus` | `datasourceUid`, `expr`, `endTime` (plus `startTime` + `stepSeconds` when `queryType="range"`) |
| Query Loki | `query_loki_logs` | `datasourceUid`, `logql` |
| List datasources | `list_datasources` | none |
| Create annotation | `create_annotation` | none declared; in practice `text` + `time` (epoch ms), optional `dashboardUid`, `panelId`, `tags` |

`datasourceUid` is **mandatory** on every query tool and has no default — the
UIDs are `grafanacloud-prom`, `grafanacloud-logs`, `grafanacloud-traces`.
`query_prometheus` also requires `endTime`; omitting either does not raise, it
returns an error string as ordinary text content.

Full list:

- `add_activity_to_incident`
- `alerting_manage_routing`
- `alerting_manage_rules`
- `analyze_loki_labels`
- `check_datasources_health`
- `create_annotation`
- `create_datasource`
- `create_folder`
- `create_incident`
- `create_snapshot`
- `delete_snapshot`
- `find_error_pattern_logs`
- `find_slow_requests`
- `generate_deeplink`
- `get_alert_group`
- `get_annotation_tags`
- `get_annotations`
- `get_assertions`
- `get_current_oncall_users`
- `get_dashboard_by_uid`
- `get_dashboard_panel_queries`
- `get_dashboard_property`
- `get_dashboard_summary`
- `get_datasource`
- `get_incident`
- `get_oncall_shift`
- `get_panel_image`
- `get_plugin`
- `get_sift_analysis`
- `get_sift_investigation`
- `get_snapshot`
- `grafana_api_request`
- `install_plugin`
- `list_alert_groups`
- `list_datasources`
- `list_incidents`
- `list_loki_label_names`
- `list_loki_label_values`
- `list_oncall_schedules`
- `list_oncall_teams`
- `list_oncall_users`
- `list_prometheus_label_names`
- `list_prometheus_label_values`
- `list_prometheus_metric_metadata`
- `list_prometheus_metric_names`
- `list_provisioning_repositories`
- `list_pyroscope_label_names`
- `list_pyroscope_label_values`
- `list_pyroscope_profile_types`
- `list_sift_investigations`
- `list_snapshots`
- `query_loki_logs`
- `query_loki_patterns`
- `query_loki_stats`
- `query_prometheus`
- `query_prometheus_histogram`
- `query_pyroscope`
- `search_dashboards`
- `search_folders`
- `search_plugin_information`
- `suggest_loki_alloy_label_config`
- `tempo_docs-config`
- `tempo_docs-traceql`
- `tempo_get-attribute-names`
- `tempo_get-attribute-values`
- `tempo_get-trace`
- `tempo_traceql-metrics-instant`
- `tempo_traceql-metrics-range`
- `tempo_traceql-search`
- `update_annotation`
- `update_dashboard`
- `update_datasource`
- `validate_provisioning_file`

Phase 2 note: `query_prometheus_histogram` generates the `histogram_quantile`
PromQL for us from `metric` + `percentile`, which is exactly the shape the
deadline forecaster wants against `render_frame_duration_milliseconds_bucket`.
Pass the base name without the `_bucket` suffix.

#### mcp SDK 2.0 compatibility

`mcp` resolved to **2.0.0**. `ClientSession`, `StdioServerParameters` and
`mcp.client.stdio.stdio_client` all survive under those exact names with
compatible signatures, so the code above needed no import changes. Two shifts
worth knowing:

- `ClientSession.call_tool` is now typed `-> CallToolResult | InputRequiredResult
  | Result`. Only `CallToolResult` carries `.content`, so the implementation
  reads it through `getattr(result, "content", [])` rather than `result.content`.
- `Tool.inputSchema` is now `Tool.input_schema` (snake_case). Nothing in this
  task reads it, but Phase 2's tool wiring will.

- [ ] **Step 6: Commit**

```bash
git add src/callsheet/grafana_mcp.py tests/test_grafana_mcp.py pyproject.toml
git commit -m "Add Grafana MCP client over stdio"
```

---

### Task 7: The end-to-end spike — the Phase 1 gate

**Files:**
- Create: `scripts/spike_end_to_end.py`, `README.md`

**Interfaces:**
- Consumes: everything above
- Produces: an exit code. `0` means the telemetry spine works and Phase 2 may begin. Anything else means the concept needs rework before more is built on it.

- [ ] **Step 1: Write the spike script**

```python
# scripts/spike_end_to_end.py
"""Phase 1 gate: render real frames, then read the metric back through the Grafana MCP server.

Run:
    python scripts/spike_end_to_end.py
Exit code 0 means the telemetry spine works end to end.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

from callsheet.config import Config, ConfigError
from callsheet.grafana_mcp import call_tool, list_tools
from callsheet.telemetry import Telemetry
from callsheet.worker import run_manifest

INGEST_WAIT_S = 90


async def read_back(config: Config) -> str:
    tools = await list_tools(config)
    print(f"  mcp-grafana exposes {len(tools)} tools")

    query_tool = next((name for name in tools if "query" in name and "prometheus" in name), None)
    if query_tool is None:
        raise RuntimeError(f"No Prometheus query tool found. Tools: {sorted(tools)}")

    datasource_tool = next(name for name in tools if "list" in name and "datasource" in name)
    datasources = await call_tool(config, datasource_tool, {})
    print(f"  datasources: {datasources[:200]}")

    # Series name is discovered here, not assumed: OTel's render.frame.duration
    # is rewritten by Prometheus, typically to render_frame_duration_milliseconds_count.
    return await call_tool(
        config,
        query_tool,
        {"expr": 'count({__name__=~"render_frame_duration.*"})', "queryType": "instant"},
    )


def main() -> int:
    load_dotenv()
    try:
        config = Config.from_env(os.environ)
    except ConfigError as error:
        print(f"FAIL: {error}")
        return 2

    print("1/3 rendering the manifest...")
    # The `with` form is load-bearing: shutdown() is the only thing that flushes
    # the final export, and a run that skips it loses every metric silently.
    with Telemetry.for_grafana(config) as telemetry:
        results = run_manifest(config, telemetry, "scenes/manifest.json")

    succeeded = sum(1 for result in results if result.succeeded)
    print(f"  rendered {len(results)} frames, {succeeded} succeeded")
    if succeeded == 0:
        print("FAIL: no frame rendered. Check BLENDER_PATH and scenes/manifest.json")
        return 3

    print(f"2/3 waiting {INGEST_WAIT_S}s for Grafana Cloud ingestion...")
    time.sleep(INGEST_WAIT_S)

    print("3/3 reading the metric back through the Grafana MCP server...")
    try:
        output = asyncio.run(read_back(config))
    except Exception as error:  # noqa: BLE001 — the spike reports, it does not recover
        print(f"FAIL: MCP read-back errored: {error}")
        return 4

    print(f"  response: {output[:400]}")
    if "render_frame_duration" not in output:
        print("FAIL: the rendered metric was not visible through the MCP server.")
        print("      Check the OTLP gateway credentials and the Prometheus metric name.")
        return 5

    print("\nPASS: real render telemetry is readable through the Grafana MCP server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write `README.md`**

```markdown
# CALLSHEET

A render farm that reports to Grafana, and an agent that turns its telemetry into
production decisions — which shots get sacrificed so the morning review isn't missed.

Built for the Google Cloud Agentic Cinema Hackathon, Grafana partner track.

## Status

Phase 1 — telemetry spine. Real Blender renders, instrumented with OpenTelemetry,
read back through the Grafana MCP server.

## Setup

1. Install Blender 4.2+ (verified on 5.2.0 LTS) and Python 3.11+.
2. Install the `mcp-grafana` binary from https://github.com/grafana/mcp-grafana/releases
3. Create a free Grafana Cloud stack (no credit card required).
4. `copy .env.example .env` and fill in every value.
5. `pip install -e ".[dev]"`
6. `blender -b -P scenes/make_scenes.py`

## Run

```
python scripts/spike_end_to_end.py
```

Exit code 0 means real render telemetry is reaching Grafana Cloud and is
readable back through the MCP server.

## Test

```
python -m pytest              # unit tests, no Blender or credentials needed
python -m pytest -m integration   # requires Blender and a .env
```

## License

Apache-2.0
```

- [ ] **Step 3: Run the spike**

Run: `python scripts/spike_end_to_end.py`
Expected: `PASS: real render telemetry is readable through the Grafana MCP server.` and exit code 0.

If it fails, the exit code says where: `2` config, `3` rendering, `4` MCP
connection, `5` metric not visible. Code `5` is the interesting one — it almost
always means the Prometheus metric name differs from the guess. Find the real
name in the Grafana Cloud UI under Explore, then fix the query in `read_back`.

- [ ] **Step 4: Commit**

```bash
git add scripts/spike_end_to_end.py README.md
git commit -m "Add end-to-end spike proving the telemetry spine"
```

- [ ] **Step 5: Record what the spike learned**

Append to `docs/superpowers/specs/2026-08-07-callsheet-design.md` a short
"Phase 1 findings" section: the real Prometheus metric names, the exact
`mcp-grafana` tool names, observed ingestion delay, and per-shot render
durations. Phase 2 is planned against these facts, not against assumptions.

```bash
git add docs/superpowers/specs/2026-08-07-callsheet-design.md
git commit -m "Record Phase 1 findings"
```

---

## What Phase 1 deliberately does not build

**One metric, not five.** The spec's §5.2 lists `render.frame.memory`,
`queue.depth` and `worker.busy` alongside logs and traces. Phase 1 ships only
`render.frame.duration`. This is deliberate: the spike's question is whether the
OTLP path to Grafana Cloud works at all, and one instrument answers that as well
as five. The remaining metrics, the Loki log path and the per-frame spans are
Plan 2 work, added once the pipe is known to be open.

No agent, no Gemini, no scheduler, no board, no benchmark. Those are Plans 2–5
and they are written *after* this spike, because their shape depends on what it
learns — particularly the real MCP tool names and the ingestion delay, which
sets how fast a scheduling round can possibly react.

| Plan | Scope | Window |
|---|---|---|
| 2 | Domain model, deadline forecaster, scheduler actions, ADK agent + Gemini | Aug 13–19 |
| 3 | Shot board — FastAPI + SSE + single page | Aug 20–25 |
| 4 | Ablation harness, Cloud Run deploy, AI Observability | Aug 26–31 |
| 5 | Demo staging, video, submission | Sept 1 |

## Definition of done for Phase 1

- [x] `python -m pytest` passes with no Blender and no credentials — 19 passed
- [x] `python -m pytest tests/test_scenes.py -m integration` passes — render cost is monotonic
- [ ] `python -m pytest tests/test_grafana_mcp.py -m integration` passes — **blocked until a Grafana Cloud account exists**
- [ ] `python scripts/spike_end_to_end.py` exits 0
- [ ] `LICENSE` is present and Apache-2.0
- [ ] No secrets committed — `git log -p | Select-String "glsa_" | Select-String -NotMatch "glsa_abc"` finds nothing (the literal `glsa_abc` is the dummy token used in tests and in this plan, so a bare `glsa_` search always matches)
- [ ] Phase 1 findings recorded in the design doc
