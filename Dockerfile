# The hosted board. Deliberately does NOT contain Blender.
#
# Rendering is local work that produces telemetry; the hosted surface is the
# agent reading that telemetry back. Everything the hackathon rules check for
# at runtime — google-genai calling Gemini, mcp-grafana talking to Grafana
# Cloud — happens in this container. Blender would add ~500MB to serve frames
# that were already rendered.
FROM python:3.12-slim

# mcp-grafana is the partner integration and is a Go binary, not a pip package.
ARG MCP_GRAFANA_VERSION=1.0.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -sL -o /tmp/mcp.tar.gz \
      "https://github.com/grafana/mcp-grafana/releases/download/v${MCP_GRAFANA_VERSION}/mcp-grafana_Linux_x86_64.tar.gz" \
 && tar -xzf /tmp/mcp.tar.gz -C /usr/local/bin mcp-grafana \
 && chmod +x /usr/local/bin/mcp-grafana \
 && rm /tmp/mcp.tar.gz \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# Editable, so the package stays at /app/src and the counted root lands on
# /app rather than in site-packages. Cheap insurance on top of
# CALLSHEET_ROOT and the cwd probe in _find_root.
RUN pip install --no-cache-dir -e .

COPY web ./web
COPY scenes/manifest.json ./scenes/manifest.json
COPY review.json ./review.json
COPY demo/frames ./demo/frames

# CALLSHEET_ROOT is the important one. Installed, the package sits in
# site-packages and cannot find the repo by walking up from __file__, so the
# page, manifest and frames all resolve into the Python install tree and the
# server serves its placeholder while looking healthy. That is what the first
# deploy did.
ENV CALLSHEET_ROOT=/app \
    CALLSHEET_DB=/tmp/callsheet.db \
    MCP_GRAFANA_PATH=/usr/local/bin/mcp-grafana \
    PYTHONUNBUFFERED=1 \
    PORT=8080
EXPOSE 8080

# $PORT is injected by Render, Cloud Run and most container hosts; 8080 is only
# the fallback for a plain `docker run`.
CMD ["sh", "-c", "python -m uvicorn callsheet.server:app --host 0.0.0.0 --port ${PORT:-8080}"]
