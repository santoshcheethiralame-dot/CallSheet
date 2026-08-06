"""Reads observed farm state out of Grafana through the MCP server."""

from __future__ import annotations

import json
import math

from callsheet.config import Config
from callsheet.domain import FarmState
from callsheet.grafana_mcp import call_tool

PROM_DATASOURCE_UID = "grafanacloud-prom"

MEAN_QUERY = (
    'sum by (shot) (rate(render_frame_duration_milliseconds_sum[1h]))'
    ' / sum by (shot) (rate(render_frame_duration_milliseconds_count[1h]))'
)


def _series(raw: str) -> list[dict]:
    """The result series, whichever of the two envelopes mcp-grafana used.

    Observed shape is the flat `{"data": [...]}`, but the Prometheus HTTP API
    it wraps returns `{"data": {"result": [...]}}`, so both are accepted rather
    than betting the forecast on an undocumented unwrapping staying put.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    data = payload.get("data", [])
    if isinstance(data, dict):          # Prometheus HTTP API shape
        data = data.get("result", [])
    return data if isinstance(data, list) else []


def _by_shot(raw: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for entry in _series(raw):
        shot = entry.get("metric", {}).get("shot")
        if not shot:
            continue
        try:
            value = float(entry["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if math.isnan(value) or math.isinf(value):
            continue
        values[shot] = value
    return values


def parse_farm_state(mean_json: str) -> FarmState:
    """Observed per-shot frame rate. Deliberately does not report progress.

    Telemetry answers "how fast is this shot?", where a longer history is
    strictly better. It cannot answer "what is left?" — the metrics counter is
    cumulative over the shot's lifetime, not over the current review. That
    question belongs to the job queue, which arrives in Plan 3.
    """
    return FarmState(mean_frame_ms=_by_shot(mean_json))


async def read_farm_state(config: Config) -> FarmState:
    mean_raw = await call_tool(config, "query_prometheus", {
        "datasourceUid": PROM_DATASOURCE_UID,
        "expr": MEAN_QUERY,
        "queryType": "instant",
        "endTime": "now",
    })
    return parse_farm_state(mean_raw)
