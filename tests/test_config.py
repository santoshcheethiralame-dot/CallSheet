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


def test_mcp_grafana_path_defaults_to_the_bare_command():
    """Optional: falls back to PATH lookup when the binary is installed normally."""
    config = Config.from_env(FULL_ENV)
    assert config.mcp_grafana_path == "mcp-grafana"


def test_mcp_grafana_path_can_be_overridden():
    config = Config.from_env({**FULL_ENV, "MCP_GRAFANA_PATH": "C:/Users/x/bin/mcp-grafana.exe"})
    assert config.mcp_grafana_path == "C:/Users/x/bin/mcp-grafana.exe"


def test_mcp_grafana_path_is_not_reported_as_missing():
    """It has a default, so its absence must never block startup."""
    with pytest.raises(ConfigError) as excinfo:
        Config.from_env({})
    assert "MCP_GRAFANA_PATH" not in str(excinfo.value)
