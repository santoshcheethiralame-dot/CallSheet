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

`MCP_GRAFANA_PATH` is optional — set it only when the `mcp-grafana` binary is not
on `PATH`; otherwise the bare command `mcp-grafana` is used.

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
