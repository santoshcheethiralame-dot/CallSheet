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
                block.text
                for block in getattr(result, "content", [])
                if getattr(block, "text", None)
            )
