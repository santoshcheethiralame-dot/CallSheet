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
