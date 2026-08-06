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
