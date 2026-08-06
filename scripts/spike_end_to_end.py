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

# Established in Task 6 against the live stack, not guessed.
PROM_DATASOURCE_UID = "grafanacloud-prom"
REQUIRED_TOOLS = ("list_datasources", "query_prometheus")

# `count by (__name__)` and not bare `count()`: a bare aggregation DROPS the
# metric name, so the caller's substring check could never match no matter how
# healthy the stack was.
PROBE_QUERY = 'count by (__name__) ({__name__=~"render_frame_duration.*"})'


async def read_back(config: Config) -> str:
    tools = await list_tools(config)
    print(f"  mcp-grafana exposes {len(tools)} tools")

    # Exact names, not substring matching: "query" + "prometheus" matches BOTH
    # query_prometheus and query_prometheus_histogram, so which one you got
    # depended on server ordering.
    missing = [name for name in REQUIRED_TOOLS if name not in tools]
    if missing:
        raise RuntimeError(f"mcp-grafana is missing {missing}. Tools: {sorted(tools)}")

    datasources = await call_tool(config, "list_datasources", {})
    print(f"  datasources: {datasources[:200]}")

    # datasourceUid and endTime are both REQUIRED. Omitting them does not raise —
    # the server returns its error as ordinary text content, which sails past any
    # try/except and gets misreported as a credentials failure.
    return await call_tool(
        config,
        "query_prometheus",
        {
            "datasourceUid": PROM_DATASOURCE_UID,
            "expr": PROBE_QUERY,
            "queryType": "instant",
            "endTime": "now",
        },
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
    for result in results:
        print(f"    {result.shot} frame {result.frame}: {result.duration_ms:.0f} ms")
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
